import sqlite3
import json
import tempfile
import unittest
from contextlib import closing
from unittest import mock
from datetime import datetime
from pathlib import Path

from fleet_node_observability.commands.collect_openclaw_sessions import collect, render, ZONE


class SessionCountsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / "private" / "starts.sqlite"
        self.dbpath = self.root / "agents/main/agent/openclaw-agent.sqlite"
        self.dbpath.parent.mkdir(parents=True)
        with closing(sqlite3.connect(self.dbpath)) as db, db:
            db.execute("CREATE TABLE session_windows (session_id TEXT PRIMARY KEY, session_key TEXT, started_at INTEGER)")
            db.execute("CREATE TABLE session_nodes (session_key TEXT PRIMARY KEY, current_session_id TEXT, entry_json TEXT)")
        self.now = datetime(2026, 3, 9, 12, tzinfo=ZONE).timestamp()

    def insert(self, identity, key, timestamp):
        with closing(sqlite3.connect(self.dbpath)) as db, db:
            db.execute("INSERT INTO session_windows VALUES (?,?,?)", (identity, key, timestamp))

    def metadata(self, identity, key, **fields):
        with closing(sqlite3.connect(self.dbpath)) as db, db:
            db.execute("INSERT OR REPLACE INTO session_nodes VALUES (?,?,?)",
                       (key, identity, json.dumps({'sessionId':identity, **fields})))

    def test_recovers_lifecycle_timestamp_without_projected_start(self):
        self.insert('old', 'agent:main:cron:job', None)
        self.metadata('old', 'agent:main:cron:job', sessionStartedAt=self.now*1000)
        before=self.dbpath.read_bytes()
        counts,undated,_=collect(self.root,self.state,self.now)
        self.assertEqual(counts['2026-03-09','all'],1)
        self.assertEqual(counts['2026-03-09','cron'],1)
        self.assertEqual(undated,0)
        self.assertEqual(self.dbpath.read_bytes(),before)

    def test_lifecycle_priority_repairs_existing_ledger_without_double_count(self):
        self.insert('old', 'agent:main:slack:x', self.now*1000)
        collect(self.root,self.state,self.now)
        original=datetime(2026,3,8,12,tzinfo=ZONE).timestamp()*1000
        self.metadata('old','agent:main:slack:x',sessionStartedAt=original)
        counts,_,_=collect(self.root,self.state,self.now)
        self.assertEqual(counts['2026-03-08','all'],1)
        self.assertEqual(counts['2026-03-09','all'],0)
        self.assertEqual(collect(self.root,self.state,self.now)[0],counts)
        # A corrected date older than the window must remove the mistaken recent count.
        self.metadata('old','agent:main:slack:x',sessionStartedAt=original-40*86400000)
        self.assertEqual(sum(collect(self.root,self.state,self.now)[0].values()),0)

    def test_reset_metadata_cannot_redate_a_previous_window(self):
        self.insert('previous','agent:main:slack:x',None)
        self.metadata('replacement','agent:main:slack:x',sessionStartedAt=self.now*1000)
        counts,undated,_=collect(self.root,self.state,self.now)
        self.assertFalse(counts)
        self.assertEqual(undated,1)

    def test_update_time_and_invalid_lifecycle_values_are_not_start_dates(self):
        for i,value in enumerate([None,0,-1,True,'1789870000000']):
            identity=str(i); key='agent:main:slack:'+identity
            self.insert(identity,key,None)
            self.metadata(identity,key,sessionStartedAt=value,updatedAt=self.now*1000,createdAt=self.now*1000)
        counts,undated,_=collect(self.root,self.state,self.now)
        self.assertFalse(counts)
        self.assertEqual(undated,5)

    def test_metadata_identity_and_future_lifecycle_timestamp_are_checked(self):
        self.insert('old','agent:main:slack:x',None)
        self.metadata('old','agent:main:slack:x',sessionId='wrong',sessionStartedAt=self.now*1000)
        self.assertEqual(collect(self.root,self.state,self.now)[1],1)
        self.metadata('old','agent:main:slack:x',sessionStartedAt=(self.now+3600)*1000)
        self.assertEqual(render('test',self.root,self.state,self.now)[1],1)

    def test_calendar_buckets_dedupe_and_cleanup_preservation(self):
        self.insert("one", "agent:main:cron:job", datetime(2026, 3, 8, 0, 1, tzinfo=ZONE).timestamp()*1000)
        self.insert("two", "agent:main:slack:x", datetime(2026, 3, 7, 23, 59, tzinfo=ZONE).timestamp()*1000)
        self.insert("unknown", "agent:main:slack:y", None)
        before = self.dbpath.read_bytes()
        counts, undated, since = collect(self.root, self.state, self.now)
        self.assertEqual(counts["2026-03-08", "cron"], 1)
        self.assertEqual(counts["2026-03-07", "all"], 1)
        self.assertEqual(undated, 1)
        self.assertEqual(self.dbpath.read_bytes(), before)
        self.assertEqual(collect(self.root, self.state, self.now)[0], counts)
        with closing(sqlite3.connect(self.dbpath)) as db, db:
            db.execute("DELETE FROM session_windows")
        self.assertEqual(collect(self.root, self.state, self.now + 10)[0], counts)
        self.assertEqual(collect(self.root, self.state, self.now + 10)[2], since)
        self.assertNotIn(b"agent:main", self.state.read_bytes())

    def test_dst_offsets_and_bounded_series(self):
        text, status = render("test", self.root, self.state, self.now)
        self.assertEqual(status, 0)
        collected = next(line for line in text.splitlines() if line.startswith('openclaw_sessions_collected_at_seconds{'))
        self.assertEqual(float(collected.rsplit(' ', 1)[1]), self.now)
        self.assertEqual(sum(line.startswith("openclaw_sessions_daily{") for line in text.splitlines()), 60)
        self.assertIn('session_day="2026-03-08T00:00:00-05:00"', text)
        self.assertIn('session_day="2026-03-09T00:00:00-04:00"', text)
        self.assertEqual(self.state.stat().st_mode & 0o777, 0o600)

    def test_missing_source_is_failure_not_zero(self):
        self.dbpath.unlink()
        text, status = render("test", self.root, self.state, self.now)
        self.assertEqual(status, 1)
        self.assertNotIn("openclaw_sessions_daily", text)
        self.assertIn('openclaw_sessions_collector_success{node="test",node_label="test"} 0', text)
        self.assertFalse(self.dbpath.exists())

    def test_future_timestamp_fails_closed(self):
        self.insert("future", "agent:main:cron:job", (self.now+3600)*1000)
        self.assertEqual(render("test", self.root, self.state, self.now)[1], 1)

    def test_cron_does_not_match_slack_text_or_double_count_runs(self):
        for identity, key in [("cron", "agent:main:cron:job:run:id"), ("user", "agent:main:slack:cron:job")]:
            self.insert(identity, key, self.now*1000)
        counts, _, _ = collect(self.root, self.state, self.now)
        self.assertEqual(counts["2026-03-09", "all"], 2)
        self.assertEqual(counts["2026-03-09", "cron"], 1)

    def test_existing_schedule_collects_sessions_even_when_cron_rpc_fails(self):
        from fleet_node_observability.commands import collect_openclaw_cron_schedule as cron
        output=self.root/'metrics/openclaw_cron_schedule.prom'
        with mock.patch('sys.argv',['collector','--node','test','--output',str(output)]), \
             mock.patch.object(cron,'collect_jobs',side_effect=cron.CollectionError('timeout','timeout')), \
             mock.patch('fleet_node_observability.commands.collect_openclaw_sessions.render',return_value=('sessions test\n',0)) as sessions:
            self.assertEqual(cron.main(),1)
        sessions.assert_called_once()
        self.assertEqual(output.with_name('openclaw_sessions.prom').read_text(),'sessions test\n')
        self.assertIn('collector_success{node="test",node_label="test"} 0',output.read_text())


if __name__ == "__main__":
    unittest.main()
