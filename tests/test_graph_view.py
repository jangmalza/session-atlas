"""Graph rendering keeps every original identity, quote and directed edge."""
from copy import deepcopy
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import context_graph as cg


class GraphViewTests(unittest.TestCase):
    def test_self_contained_view_preserves_all_data_including_hostile_text(self):
        graph={'schemaVersion':'mvp-1','projects':['예시'],'sessionCount':1,'nodes':[
            {'id':f'n-{index}','title':f'항목 {index} __GRAPH_SCRIPT__ __KO_LABELS__ __QA_CONTEXT__ </script><img src=x>',
             'summary':'원문을 바꾸지 않는 화면','project':'예시','classification':'fact','status':'none',
             'provenance':[{'session_id':'test','source_node':f's-{index}','evidence':[{'messageId':'m1','quote':'<원문> & 인용','verified':True}]}]}
            for index in range(304)],'edges':[{'id':'r1','from':'n-303','to':'n-0','type':'answers','sessions':['test']}]}
        before=deepcopy(graph)
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'graph.html'
            cg.render_html(graph,path)
            document=path.read_text(encoding='utf-8')
        self.assertEqual(json.loads(re.search(r'id="graph-data">(.*?)</script>',document,re.S)[1]),before)
        self.assertEqual(graph,before)
        self.assertNotIn('<img src=x>',document)
        self.assertNotRegex(document,r'<(?:script|link)[^>]+(?:src|href)=')
        self.assertIn('aria-label="분류 필터"',document)
        self.assertEqual(graph['edges'][0]['from'],'n-303')

    def test_offline_question_metadata_is_separate_from_immutable_graph_and_escaped(self):
        graph={'schemaVersion':'mvp-1','projects':[],'sessionCount':0,'nodes':[],'edges':[]}
        context={'s':{'session':{'title':'__GRAPH_DATA__ </script><img src=x>'},'messages':[],
                      'analysis':{'anchors':{},'relations':[]}}}
        before=deepcopy((graph,context))
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'graph.html'
            cg.render_html(graph,path,context)
            document=path.read_text(encoding='utf-8')
        self.assertEqual(json.loads(re.search(r'id="graph-data">(.*?)</script>',document,re.S)[1]),graph)
        self.assertEqual(json.loads(re.search(r'id="qa-context">(.*?)</script>',document,re.S)[1]),context)
        self.assertEqual((graph,context),before)
        self.assertNotIn('<img src=x>',document)


if __name__=='__main__':unittest.main()
