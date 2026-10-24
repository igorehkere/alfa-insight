export function getAnalysisFactors(fields: string[]): string[] {
  const result = new Set<string>();

  for (const field of fields) {
    const value = field.toLowerCase();

    if (value.includes('industry') || value.includes('отрасл')) {
      result.add('отрасль');
    }

    if (value.includes('region') || value.includes('регион')) {
      result.add('регион');
    }

    if (
      value.includes('product') ||
      value.includes('loan') ||
      value.includes('settlement') ||
      value.includes('продукт')
    ) {
      result.add('банковские продукты');
    }

    if (value.includes('payment') || value.includes('платеж')) {
      result.add('платёжные операции');
    }

    if (value.includes('counterpart') || value.includes('контрагент')) {
      result.add('контрагентов');
    }
  }

  return [...result];
}
