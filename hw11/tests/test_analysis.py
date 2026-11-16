import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from analyze import summary,quantile,histogram_quantile,histogram_comparison
from verify_evidence import verify


class AnalysisTests(unittest.TestCase):
    def test_quantiles_and_single_chunk_have_no_inter_chunk_latency(self):
        self.assertEqual(quantile([1,2,3,4],.5),2.5)
        self.assertIsNone(quantile([],.95))
        row={'outcome':'completed','sent_mono':1,'ttfc_seconds':.1,
             'total_seconds':.1,'queue_seconds':.01,'inter_chunk_seconds':[]}
        result=summary([row])
        self.assertEqual(result['completed'],1)
        self.assertIsNone(result['inter_chunk_seconds_p95'])

    def test_success_percentile_does_not_hide_error_denominator(self):
        rows=[{'outcome':'completed','sent_mono':1,'ttfc_seconds':.2,'total_seconds':.3,
               'queue_seconds':.01},{'outcome':'timed_out','sent_mono':2},
              {'outcome':'generator_dropped','sent_mono':None}]
        result=summary(rows)
        self.assertEqual(result['planned'],3)
        self.assertEqual(result['sent'],2)
        self.assertEqual(result['completed'],1)
        self.assertAlmostEqual(result['error_fraction'],2/3)
        self.assertEqual(result['ttfc_seconds_p95'],.2)

    def test_classic_histogram_aggregates_cumulative_buckets(self):
        self.assertAlmostEqual(histogram_quantile({'0.1':10,'0.2':20,'+Inf':20},.95),.19)
        self.assertEqual(histogram_quantile({'0.1':0,'0.2':1,'+Inf':20},.95),.2)
        self.assertIsNone(histogram_quantile({'0.1':0,'+Inf':0},.95))

    def test_comparison_rejects_restarted_pod_cohort(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp)
            def snapshot(pod):
                return {'queries':{'hw11_ttfc_seconds_bucket':{'data':{'result':[
                    {'metric':{'pod':pod,'le':'+Inf'},'value':[0,'1']} ]}}}}
            (directory/'test-metrics-before.json').write_text(json.dumps(snapshot('old')))
            (directory/'test-metrics-after.json').write_text(json.dumps(snapshot('new')))
            result=histogram_comparison(directory,'test',[])
            self.assertFalse(result['available'])

    def test_empty_directory_cannot_pass_submission_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            result=verify(Path(tmp))
            self.assertFalse(result['complete'])
            self.assertIn('Нет сырых Kubernetes-запросов requests.jsonl',result['missing'])
            self.assertEqual(result['requests'],0)
            self.assertFalse((Path(tmp)/'requests.jsonl').exists())
