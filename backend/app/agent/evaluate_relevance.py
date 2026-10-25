"""Offline, reproducible pair metrics. No network, index creation or model calls."""
import json
from pathlib import Path
from app.agent.recommendations import rank_news

DATA = Path(__file__).with_name('data') / 'relevance_cases.json'


def evaluate(rank=rank_news):
    data = json.loads(DATA.read_text())
    profiles = {row['company_id']: row for row in data['profiles']}
    news = {row['id']: row for row in data['news']}
    result = {}
    for split in ('development', 'held_out'):
        rows = [row for row in data['cases'] if row['split'] == split]
        tp = fp = fn = high_false = returned = correct = slots = 0
        for case in rows:
            items = rank(profiles[case['profile']], [news[case['news']]])
            tp += bool(items) and case['relevant']
            fp += bool(items) and not case['relevant']
            fn += not items and case['relevant']
            high_false += bool(items) and items[0]['relevance_score'] >= 20 and not case['relevant']
        for identifier, profile in profiles.items():
            cases = {row['news']: row['relevant'] for row in rows if row['profile'] == identifier}
            slots += min(5, len(cases))
            for item in rank(profile, [news[nid] for nid in cases], limit=5):
                returned += 1
                correct += cases[item['id']]
        result[split] = {'pairs': len(rows), 'positives': sum(row['relevant'] for row in rows),
                         'true_positive': tp, 'false_positive': fp, 'false_negative': fn,
                         'precision': round(tp / (tp + fp), 4) if tp + fp else None,
                         'positive_recall': round(tp / (tp + fn), 4) if tp + fn else None,
                         'precision_at_5_slots': round(correct / slots, 4) if slots else None,
                         'precision_at_5_returned': round(correct / returned, 4) if returned else None,
                         'returned_at_5': returned, 'high_false_cross_industry': high_false}
    return result


if __name__ == '__main__':
    print(json.dumps(evaluate(), ensure_ascii=False, indent=2))
