"""Small reproducible Decimal calculator. No model expressions or inferred inputs."""
from dataclasses import dataclass, asdict
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext


@dataclass(frozen=True)
class Quantity:
    value: Decimal
    unit: str
    origin: str
    period: str = ''

    def __post_init__(self):
        if not isinstance(self.value, Decimal):
            raise ValueError('Величина должна быть Decimal')
        if not self.value.is_finite() or self.value < 0 or self.value > Decimal('1e18'):
            raise ValueError('Недопустимая величина')
        if self.unit not in {'rub', 'usd', 'eur', 'count', 'month', 'percent'} or not self.origin:
            raise ValueError('Нужны известные единицы и происхождение')
        if self.period not in {'', 'month', 'year', 'day'}:
            raise ValueError('Неизвестный период')
        if self.unit == 'percent' and self.value > 100:
            raise ValueError('Процент вне диапазона')


def calculate(operation, inputs):
    """Return a ledger entry with fixed precision, independent of caller context."""
    with localcontext() as context:
        context.prec = 40
        return _calculate(operation, inputs)


def _calculate(operation, inputs):
    required = {'available': ('budget', 'reserve'), 'cost': ('unit_price', 'quantity'),
                'net': ('revenue', 'costs'), 'difference': ('first', 'second'),
                'payback': ('investment', 'net')}
    if operation not in required or any(k not in inputs for k in required[operation]):
        raise ValueError('Неизвестная операция или отсутствуют входные величины')
    a, b = (inputs[k] for k in required[operation])
    money = {'rub', 'usd', 'eur'}
    if operation == 'cost':
        if a.unit not in money or b.unit != 'count' or b.value != b.value.to_integral_value() or b.period:
            raise ValueError('Стоимость и целое количество имеют несовместимые единицы')
        value, unit, period, formula = a.value * b.value, a.unit, a.period, 'unit_price * quantity'
    elif operation == 'payback':
        if a.unit not in money or a.unit != b.unit or a.period or b.period != 'month' or b.value <= 0:
            raise ValueError('Окупаемость требует разовой инвестиции и положительного месячного результата')
        value, unit, period, formula = a.value / b.value, 'month', '', 'investment / monthly_net'
    else:
        if a.unit not in money or a.unit != b.unit or a.period != b.period:
            raise ValueError('Валюта или периоды несовместимы')
        value, unit, period = a.value - b.value, a.unit, a.period
        formula = ' - '.join(required[operation])
        if operation == 'available' and value < 0:
            raise ValueError('Резерв превышает бюджет')
    if not value.is_finite() or abs(value) > Decimal('1e18'):
        raise ValueError('Результат вне допустимого диапазона')
    value = value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    return {'operation': operation, 'inputs': {k: {**asdict(inputs[k]), 'value': str(inputs[k].value)} for k in required[operation]},
            'formula': formula, 'value': str(value), 'unit': unit, 'period': period,
            'rounding': '0.01 HALF_UP', 'origin': 'local_calculation',
            'assumption': 'Простая окупаемость при неизменном месячном результате; без дисконтирования' if operation == 'payback' else ''}


def ledger_for(facts):
    quantities = {}
    for key, fact in facts.items():
        if isinstance(fact, dict) and 'value' in fact:
            try:
                quantities[key] = Quantity(Decimal(fact['value']), fact['unit'], fact['message'], fact.get('period', ''))
            except (ValueError, InvalidOperation, KeyError):
                continue
    ledger = []
    for operation in ('available', 'cost', 'net'):
        try:
            ledger.append(calculate(operation, quantities))
        except ValueError:
            pass
    if 'investment' in quantities and 'net' in {row['operation'] for row in ledger}:
        net = next(row for row in ledger if row['operation'] == 'net')
        try:
            ledger.append(calculate('payback', {'investment': quantities['investment'],
                'net': Quantity(Decimal(net['value']), net['unit'], 'local net', net['period'])}))
        except ValueError:
            pass
    return ledger


def calculation_presentation(ledger):
    """Model context omits repeated raw messages; the full local audit retains them."""
    return [{**{key: value for key, value in row.items() if key != 'inputs'},
             'inputs': {name: {key: value for key, value in quantity.items() if key != 'origin'}
                        for name, quantity in row['inputs'].items()}} for row in ledger]
