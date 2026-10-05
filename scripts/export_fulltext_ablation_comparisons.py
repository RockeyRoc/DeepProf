"""Derive decision rates and matched transitions from the completed frozen batch."""
from __future__ import annotations

from collections import Counter
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.annotate_fulltext_rag import write_csv
from scripts.ocr_full_textbook import digest, write_json
from scripts.run_m3_fulltext_rag import RAW_RESEARCH

OUT = ROOT / 'docs/experiments/bkt-rag-improvement-20261001'
SPLITS = ['historical', 'development', 'sealed_test']
CONDITIONS = ['retrieval-on_constraint-on', 'retrieval-on_constraint-off',
              'retrieval-off_constraint-on', 'retrieval-off_constraint-off']


def main():
    summary_path = OUT / 'data/fulltext-rag-summary.json'
    summary = json.loads(summary_path.read_text(encoding='utf-8'))
    assert summary['matrix_complete'] and summary['configuration_eligible']
    batch = RAW_RESEARCH / summary['batch_id']
    question_path = batch / 'ai-annotation/questions-120-ai.csv'
    import csv
    with question_path.open(encoding='utf-8-sig', newline='') as stream:
        questions = {r['case_id']: r for r in csv.DictReader(stream)}
    cells, sources, cell_hashes = {}, {}, {}
    for path in sorted((batch / 'runs').glob('*/cells/*.json')):
        cell = json.loads(path.read_text(encoding='utf-8'))
        if cell.get('phase') not in CONDITIONS:
            continue
        key = cell['case_id'], cell['phase']
        assert key not in cells
        assert cell['status'] == 'completed'
        assert cell['evaluation_status'] in {'completed', 'not_applicable'}
        assert int(cell['model_calls']) in {0, 1}
        assert cell['frozen_config']['experiment_config_sha256'] == summary['configuration']['config_sha256']
        cells[key] = cell
        sources[str(path.relative_to(batch)).replace('\\', '/')] = digest(path)
        cell_hashes[key] = digest(path)
    assert len(cells) == 480 and len(questions) == 120
    assert Counter(r['split'] for r in questions.values()) == dict.fromkeys(SPLITS, 40)
    assert all((case_id, condition) in cells for case_id in questions for condition in CONDITIONS)

    decisions = []
    for (case_id, condition), cell in sorted(cells.items()):
        q = questions[case_id]
        assert cell['split'] == q['split']
        assert q['answerability'] in {'answerable', 'insufficient_evidence'}
        decisions.append({'case_id': case_id, 'condition': condition, 'split': cell['split'],
                          'answerability': q['answerability'], 'generated': int(cell['model_calls'] > 0),
                          'cell_sha256': cell_hashes[case_id, condition],
                          'annotation_source': 'ai', 'human_review_status': 'not_reviewed'})
    write_csv(OUT / 'data/fulltext-ablation-decisions.csv', decisions)

    rates = []
    for condition in CONDITIONS:
        for split in SPLITS:
            selected = [r for r in decisions if r['condition'] == condition and r['split'] == split]
            assert len(selected) == 40
            for metric, answerability, outcome in [
                ('false_accept_generation', 'insufficient_evidence', 1),
                ('false_reject_no_generation', 'answerable', 0),
            ]:
                relevant = [r for r in selected if r['answerability'] == answerability]
                n = sum(r['generated'] == outcome for r in relevant)
                d = len(relevant)
                expected = summary['conditions_by_split'][condition][split][metric]
                assert (n, d) == (expected['numerator'], expected['denominator'])
                rates.append({'condition': condition, 'split': split, 'metric': metric,
                              'numerator': n, 'denominator': d, 'value': n / d if d else None,
                              'rule_zero_generation': condition == 'retrieval-off_constraint-on'})
    write_csv(OUT / 'data/fulltext-ablation-rates.csv', rates)

    transitions = []
    for retrieval in ['on', 'off']:
        for split in SPLITS:
            for answerability in ['insufficient_evidence', 'answerable']:
                case_ids = [k for k, r in questions.items() if r['split'] == split and r['answerability'] == answerability]
                counts = Counter((int(cells[k, f'retrieval-{retrieval}_constraint-off']['model_calls'] > 0),
                                  int(cells[k, f'retrieval-{retrieval}_constraint-on']['model_calls'] > 0))
                                 for k in case_ids)
                assert sum(counts.values()) == len(case_ids)
                for (before, after), name in [((1, 1), 'generated_both'), ((1, 0), 'generation_stopped'),
                                            ((0, 1), 'generation_started'), ((0, 0), 'neither_generated')]:
                    transitions.append({'retrieval': retrieval, 'split': split, 'answerability': answerability,
                                        'transition': name, 'count': counts[before, after],
                                        'denominator': len(case_ids),
                                        'value': counts[before, after] / len(case_ids) if case_ids else None})
    write_csv(OUT / 'data/fulltext-ablation-transitions.csv', transitions)
    write_json(OUT / 'data/fulltext-ablation-figure-contract.json', {
        'batch_id': summary['batch_id'], 'backend': 'R', 'archetype': 'two independent single-panel quantitative charts',
        'conclusions': {'10': 'Evidence constraint reduces insufficient-evidence generation but has a rule-induced no-generation cost without retrieval.',
                        '11': 'Within-question constraint transitions distinguish changed decisions from unchanged decisions in each answerability partition.'},
        'unit': '120 constructed questions, 480 unique condition cells, 240 matched constraint comparisons',
        'paired_direction': 'constraint off -> constraint on, separately with retrieval on and off',
        'measure': 'whether generation was invoked; not factual correctness or complete answers',
        'uncertainty': 'descriptive finite constructed set, one run per cell; no seeds, inferential intervals or p values',
        'alignment': 'not applicable: each figure has exactly one plot area',
        'exclusions': 'no formal cells or answerability labels excluded; preflight excluded from formal comparisons',
        'human_review': 'not_reviewed; all answerability labels are AI judgments',
        'exports': '183 x 115 mm; SVG/PDF and 600 dpi PNG',
        'source_summary_sha256': digest(summary_path), 'question_annotations_sha256': digest(question_path),
        'source_cell_sha256': sources,
        'source_data_sha256': {p.name: digest(p) for p in (OUT/'data').glob('fulltext-ablation-*.csv')},
    })
    print(json.dumps({'formal_cells': len(cells), 'rate_points': len(rates),
                      'matched_questions_per_retrieval': 120, 'model_requests_sent': 0}))


if __name__ == '__main__':
    main()
