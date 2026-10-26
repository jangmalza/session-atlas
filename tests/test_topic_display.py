"""Reviewed display groups are exact-ID, source-bound, read-only projections."""
from copy import deepcopy
from contextlib import closing
import json
from pathlib import Path
import sys
import tempfile
import unittest
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from decisions_topics import map_fingerprint
from topic_display import TopicDisplayConflict, display_revision, project_topic_display, read_topic_display, set_topic_display, read_session_names, set_session_display, set_item_display, item_label_metadata


class TopicDisplayTests(unittest.TestCase):
    def setUp(self):
        self.source={'nodes':[{'id':'one','kind':'topic','title':'Same title','childIds':['item']},
                              {'id':'two','kind':'topic','title':'Same title'},
                              {'id':'item','kind':'item','parentId':'one','evidence':[{'quote':'PRIVATE QUOTE'}]}],
                     'relations':[{'id':'saved-edge','from':'item','to':'one'}]}
        self.structure=deepcopy(self.source['nodes'])
        self.preference={'sourceMapSha256':map_fingerprint(self.source),'auxiliaryTopicIds':['one']}

    def test_marks_only_selected_ids_without_mutating_any_source_field(self):
        before=deepcopy((self.source,self.structure,self.preference))
        shown,status=project_topic_display(self.source,self.structure,self.preference)
        self.assertEqual(shown[0]['displayGroup'],'auxiliary')
        self.assertNotIn('displayGroup',shown[1])
        self.assertEqual(shown[2],self.structure[2])
        self.assertEqual(status,{'status':'active','auxiliaryCount':1})
        self.assertEqual((self.source,self.structure,self.preference),before)

    def test_changed_source_shows_all_topics_instead_of_hiding_unreviewed_data(self):
        self.source['nodes'][0]['title']='Changed source'
        shown,status=project_topic_display(self.source,self.structure,self.preference)
        self.assertEqual(shown,self.structure)
        self.assertEqual(status['status'],'stale')

    def test_missing_item_or_duplicate_ids_cannot_hide_topics(self):
        for identifiers in (['missing'],['item'],['one','one'],[None],None):
            with self.subTest(identifiers=identifiers):
                shown,status=project_topic_display(self.source,self.structure,{**self.preference,'auxiliaryTopicIds':identifiers})
                self.assertEqual(shown,self.structure)
                self.assertEqual(status['status'],'invalid')

    def test_missing_preference_keeps_every_topic_visible(self):
        shown,status=project_topic_display(self.source,self.structure,None)
        self.assertEqual(shown,self.structure)
        self.assertEqual(status['status'],'default')

    def test_database_specific_sidecar_reads_only_explicit_sessions(self):
        with tempfile.TemporaryDirectory() as folder:
            db=Path(folder)/'collection.db';sidecar=db.with_suffix('.topic-display.json')
            sidecar.write_text(json.dumps({'schemaVersion':'topic-display-1','sessions':{'selected-session':self.preference}}),encoding='utf-8')
            self.assertEqual(read_topic_display(db),{'selected-session':self.preference})
            self.assertEqual(read_topic_display(Path(folder)/'other.db'),{})
            self.assertFalse(db.exists())

    def test_malformed_sidecar_never_hides_original_topics(self):
        with tempfile.TemporaryDirectory() as folder:
            db=Path(folder)/'collection.db'
            for payload in ('broken','[]','null','{"schemaVersion":"other","sessions":{}}'):
                db.with_suffix('.topic-display.json').write_text(payload,encoding='utf-8')
                self.assertEqual(read_topic_display(db),{})


class TopicDisplayWriteTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.database=Path(temp.name)/'collection.db'
        self.path=self.database.with_suffix('.topic-display.json')
        self.analysis={'nodes':[{'id':'parent','kind':'topic','title':'Original parent','childIds':['child']},
            {'id':'child','kind':'topic','title':'Original topic','parentId':'parent'},
            {'id':'other','kind':'topic','title':'Original topic'},
            {'id':'item','kind':'item','title':'Original item','evidence':[{'quote':'PRIVATE ORIGINAL'}]}],
            'relations':[{'id':'existing','from':'item','to':'child'}]}
        with closing(sqlite3.connect(self.database)) as con, con:
            con.execute('CREATE TABLE sessions(id TEXT PRIMARY KEY)')
            con.executemany('INSERT INTO sessions VALUES (?)',[(sid,) for sid in ('s','other-session','pending-session')])
            con.execute('CREATE TABLE session_maps(session_id TEXT PRIMARY KEY,map_json TEXT)')
            con.executemany('INSERT INTO session_maps VALUES (?,?)',[(sid,json.dumps(self.analysis)) for sid in ('s','other-session')])
        self.source_hash=map_fingerprint(self.analysis)
        self.original=self.database.read_bytes()

    def edit(self,topic='child',group='auxiliary',session='s',revision=None):
        return set_topic_display(self.database,session=session,topic=topic,group=group,
            source_map_sha256=self.source_hash,expected_revision=revision or display_revision(read_topic_display(self.database).get(session)))

    def rename(self,name,topic='child',session='s',revision=None):
        return set_topic_display(self.database,session=session,topic=topic,display_name=name,
            source_map_sha256=self.source_hash,expected_revision=revision or display_revision(read_topic_display(self.database).get(session)))

    def session_name(self,name,session='s',revision=None):
        return set_session_display(self.database,session=session,display_name=name,
            expected_revision=revision or display_revision(read_session_names(self.database).get(session)))

    def item_title(self,title,session='s',item='item',revision=None):
        return set_item_display(self.database,session=session,item=item,display_title=title,
            source_map_sha256=self.source_hash,expected_revision=revision or display_revision(read_topic_display(self.database).get(session)))

    def test_item_title_is_separate_from_analysis_and_survives_other_preference_edits(self):
        result=self.item_title('질문·답변 표시 제목')
        self.assertEqual(result['itemLabels']['item'],{'title':'Original item','displayTitle':'질문·답변 표시 제목'})
        self.edit('child');self.rename('주제 이름');self.session_name('세션 이름')
        self.assertEqual(read_topic_display(self.database)['s']['itemTitles']['item'],'질문·답변 표시 제목')
        self.item_title(None)
        preference=read_topic_display(self.database)['s']
        self.assertNotIn('itemTitles',preference)
        self.assertEqual(preference['auxiliaryTopicIds'],['child'])
        self.assertEqual(preference['displayNames']['child'],'주제 이름')
        self.assertEqual(read_session_names(self.database)['s'],'세션 이름')
        self.assertEqual(self.database.read_bytes(),self.original)

    def test_item_alias_is_scoped_to_the_original_session_and_source_id(self):
        self.item_title('<img src=x> 표시 제목')
        self.assertNotIn('other-session',read_topic_display(self.database))
        with self.assertRaises(KeyError):self.item_title('이름',item='child')
        with self.assertRaises(TopicDisplayConflict):self.item_title('오래된 편집',revision=display_revision(None))
        self.item_title('Original item')
        self.assertNotIn('itemTitles',read_topic_display(self.database)['s'])
        self.assertEqual(self.database.read_bytes(),self.original)

    def test_invalid_item_label_mapping_never_alters_original_structure(self):
        for mapping in ({'missing':'Name'},{'child':'Topic is not an item'},{'item':''}):
            preference={'sourceMapSha256':self.source_hash,'auxiliaryTopicIds':[],'itemTitles':mapping}
            shown,state=project_topic_display(self.analysis,self.analysis['nodes'],preference)
            self.assertEqual(state['status'],'invalid')
            self.assertEqual(shown,self.analysis['nodes'])

    def test_unanalysed_sessions_can_be_named_and_restored_without_database_changes(self):
        result=self.session_name('표시할 세션',session='pending-session')
        self.assertEqual(result['sessionDisplay']['status'],'active')
        self.assertEqual(read_session_names(self.database)['pending-session'],'표시할 세션')
        self.session_name(None,session='pending-session')
        self.assertEqual(read_session_names(self.database),{})
        self.assertEqual(self.database.read_bytes(),self.original)

    def test_session_and_topic_edits_share_the_file_without_losing_other_preferences(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs=[pool.submit(self.edit,'child'),pool.submit(self.session_name,'새 세션 이름')]
            for job in jobs:self.assertTrue(job.result()['saved'])
        self.rename('주제 별칭');self.session_name('다른 세션',session='other-session')
        before=read_topic_display(self.database)
        self.session_name(None)
        self.assertEqual(read_topic_display(self.database),before)
        self.assertEqual(read_session_names(self.database),{'other-session':'다른 세션'})
        self.assertEqual(self.database.read_bytes(),self.original)

    def test_stale_or_unknown_session_edits_cannot_overwrite_names(self):
        self.session_name('저장된 이름');before=self.path.read_bytes()
        with self.assertRaises(TopicDisplayConflict):self.session_name('이전 화면 편집',revision=display_revision(None))
        with self.assertRaises(KeyError):self.session_name('이름',session='unknown')
        with self.assertRaises(ValueError):self.session_name('bad\nname')
        self.assertEqual(self.path.read_bytes(),before)

    def test_failed_session_write_keeps_previous_file_and_all_source_rows(self):
        self.session_name('기존 표시 이름');before=self.path.read_bytes()
        with patch('topic_display.os.replace',side_effect=OSError('synthetic failure')),self.assertRaises(OSError):self.session_name('변경할 이름')
        self.assertEqual(self.path.read_bytes(),before)
        self.assertEqual(self.database.read_bytes(),self.original)

    def test_custom_name_and_restoration_preserve_original_titles_and_group_settings(self):
        self.edit('child');self.rename('다른 표시 이름',topic='other')
        result=self.rename('  새 주제 이름  ')
        self.assertEqual(result['displayName'],'새 주제 이름')
        preference=read_topic_display(self.database)['s']
        shown,_=project_topic_display(self.analysis,self.analysis['nodes'],preference)
        self.assertEqual(shown[1]['title'],'Original topic')
        self.assertEqual(shown[1]['displayTitle'],'새 주제 이름')
        self.assertEqual(shown[1]['displayGroup'],'auxiliary')
        self.rename(None)
        preference=read_topic_display(self.database)['s']
        self.assertNotIn('child',preference['displayNames'])
        self.assertEqual(preference['displayNames']['other'],'다른 표시 이름')
        self.assertEqual(preference['auxiliaryTopicIds'],['child'])
        self.assertEqual(self.database.read_bytes(),self.original)

    def test_group_moves_keep_saved_names_and_original_name_save_removes_only_that_alias(self):
        self.rename('별칭');self.edit('child')
        self.assertEqual(read_topic_display(self.database)['s']['displayNames']['child'],'별칭')
        self.edit('child','primary')
        self.assertEqual(read_topic_display(self.database)['s']['displayNames']['child'],'별칭')
        self.rename('Original topic')
        self.assertNotIn('displayNames',read_topic_display(self.database)['s'])

    def test_invalid_names_and_unknown_topics_cannot_replace_settings(self):
        self.rename('저장된 이름');before=self.path.read_bytes()
        for name in ('','   ','x'*121,'bad\nname','bad\tname','bad\u2028name','bad\x00name',True,12,[]):
            with self.subTest(name=repr(name)),self.assertRaises(ValueError):self.rename(name)
        with self.assertRaises(KeyError):self.rename('이름',topic='item')
        with self.assertRaises(TopicDisplayConflict):self.rename('오래된 편집',revision=display_revision(None))
        self.assertEqual(self.path.read_bytes(),before)

    def test_html_like_alias_is_literal_metadata_scoped_to_one_session_and_id(self):
        label='<img src=x onerror=alert(1)> & "별칭"'
        self.rename(label)
        preference=read_topic_display(self.database)['s']
        shown,_=project_topic_display(self.analysis,self.analysis['nodes'],preference)
        self.assertEqual(shown[1]['displayTitle'],label)
        self.assertNotIn('displayTitle',shown[2])
        self.assertNotIn('other-session',read_topic_display(self.database))
        self.assertEqual(self.database.read_bytes(),self.original)

    def test_move_restore_and_new_reader_persist_only_the_display_file(self):
        moved=self.edit()
        self.assertEqual(moved['auxiliaryTopicIds'],['child'])
        restored=self.edit(group='primary')
        self.assertEqual(restored['auxiliaryTopicIds'],[])
        self.assertEqual(read_topic_display(self.database)['s']['primaryTopicIds'],['child'])
        self.assertEqual(self.database.read_bytes(),self.original)

    def test_stale_revision_and_source_never_overwrite_newer_preferences(self):
        self.edit();before=self.path.read_bytes()
        with self.assertRaises(TopicDisplayConflict):self.edit('other',revision=display_revision(None))
        with self.assertRaises(TopicDisplayConflict):
            set_topic_display(self.database,session='s',topic='other',group='primary',source_map_sha256='0'*64,
                              expected_revision=display_revision(read_topic_display(self.database)['s']))
        self.assertEqual(self.path.read_bytes(),before)

    def test_only_existing_topic_ids_can_be_edited(self):
        for topic in ('missing','item'):
            with self.assertRaises(KeyError):self.edit(topic)
        self.assertFalse(self.path.exists())
        self.assertEqual(self.database.read_bytes(),self.original)

    def test_corrupted_preferences_are_preserved_for_review(self):
        self.path.write_text('BROKEN PRIVATE SETTINGS',encoding='utf-8')
        with self.assertRaises(TopicDisplayConflict):self.edit()
        self.assertEqual(self.path.read_text(encoding='utf-8'),'BROKEN PRIVATE SETTINGS')

    def test_parallel_session_edits_merge_without_losing_either_session(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda sid:self.edit(session=sid),('s','other-session')))
        self.assertTrue(all(result['saved'] for result in results))
        self.assertEqual(set(read_topic_display(self.database)),{'s','other-session'})
        self.assertEqual(self.database.read_bytes(),self.original)

    def test_child_can_be_restored_without_moving_its_auxiliary_parent(self):
        self.edit('parent');self.edit('child','primary')
        preference=read_topic_display(self.database)['s']
        self.assertEqual(preference['auxiliaryTopicIds'],['parent'])
        shown,_=project_topic_display(self.analysis,self.analysis['nodes'],preference)
        self.assertEqual(shown[0]['displayGroup'],'auxiliary')
        self.assertEqual(shown[1]['displayGroup'],'primary')

    def test_failed_atomic_replace_keeps_previous_settings(self):
        self.edit();before=self.path.read_bytes()
        with patch('topic_display.os.replace',side_effect=OSError('synthetic failure')), self.assertRaises(OSError):self.edit('other')
        self.assertEqual(self.path.read_bytes(),before)
        self.assertEqual(list(self.path.parent.glob('*.tmp')),[])


if __name__=='__main__':unittest.main()
