"""Offline chart checks. Never request account identities or real tokens."""
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from xml.etree import ElementTree

from scripts import update_star_history as chart


DAY = '2026-11-19T00:00:00Z'


def page(total, n, cursor=None):
    return {'stargazerCount': total, 'stargazers': {
        'edges': [{'starredAt': DAY} for _ in range(n)],
        'pageInfo': {'hasNextPage': cursor is not None, 'endCursor': cursor}}}


class Stars(unittest.TestCase):
    def fetch(self, *pages):
        self.transport = Mock(side_effect=pages)
        return chart.fetch_star_times('fixture', 'owner/repo', transport=self.transport, sleep=lambda _: None)

    def test_paginated_shortfall_keeps_official_total_and_real_dates(self):
        total, dates = self.fetch(page(569, 100, 'next'), page(569, 468))
        self.assertEqual((total, len(dates)), (569, 568))
        self.assertEqual(self.transport.call_args.args[1]['cursor'], 'next')
        svg = chart.render_svg('owner/repo', total, dates)
        ElementTree.fromstring(svg)
        for label in ('569 stars', '568 dated stars plotted', '1 without a visible timestamp'):
            self.assertIn(label, svg)
        self.assertNotIn('fixture', svg)
        self.assertEqual(chart.daily_series(dates)[-1][1], 568)

    def test_exact_and_empty(self):
        for n in (0, 3):
            total, dates = self.fetch(page(n, n))
            self.assertEqual(total, len(dates))
            ElementTree.fromstring(chart.render_svg('owner/repo', total, dates))

    def test_persistent_inconsistency_never_rewrites_good_file(self):
        for total, n in ((570, 530), (8, 9), (1, 0)):
            bad = page(total, n)
            with self.assertRaises(RuntimeError):
                self.fetch(bad, bad, bad, bad)
            self.assertEqual(self.transport.call_count, 4)
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / 'stars.svg'
            out.write_bytes(b'previous verified chart')
            with patch.object(chart, 'OUTPUT_PATH', out), patch.dict(chart.os.environ, GITHUB_TOKEN='fixture',
                    GITHUB_REPOSITORY='owner/repo'), patch.object(chart, 'fetch_star_times', side_effect=RuntimeError):
                with self.assertRaises(RuntimeError):
                    chart.main()
            self.assertEqual(out.read_bytes(), b'previous verified chart')

    def test_never_loops_on_broken_cursors(self):
        repeated = page(8, 1, 'x')
        with self.assertRaisesRegex(RuntimeError, 'cursor'):
            self.fetch(repeated, repeated)
        bad = page(8, 1, '')
        bad['stargazers']['pageInfo']['endCursor'] = None
        with self.assertRaisesRegex(RuntimeError, 'cursor'):
            self.fetch(bad)

    def test_malformed_data_is_not_silently_counted_as_an_undated_star(self):
        for stamp in (None, '', 'wrongZ', '2026-02-31T00:00:00Z'):
            bad = page(2, 2)
            bad['stargazers']['edges'][1]['starredAt'] = stamp
            with self.assertRaises(ValueError):
                self.fetch(bad)
        with self.assertRaises(ValueError):
            self.fetch(page(-1, 0))

    def test_refetch_recovers_without_appending_previous_partial_results(self):
        total, stars = self.fetch(page(12, 2), page(12, 12))
        self.assertEqual((total, len(stars)), (12, 12))

    def test_transient_and_terminal_http(self):
        def err(code):
            return HTTPError('https://api.github.com/graphql', code, 'fixture', {}, None)
        self.assertEqual(self.fetch(err(503), err(503), page(1,1))[0], 1)
        with self.assertRaises(HTTPError):
            self.fetch(*(err(503) for _ in range(4)))
        with self.assertRaises(HTTPError):
            self.fetch(err(403))
        self.assertEqual(self.transport.call_count, 1)

    def test_fingerprint_tracks_aggregate_not_input_order_or_clock(self):
        stamps = [DAY, DAY.replace('19', '20')]
        self.assertEqual(chart.history_hash(2, stamps), chart.history_hash(2, stamps[::-1]))
        self.assertNotEqual(chart.history_hash(3, stamps), chart.history_hash(2, stamps))
        self.assertNotEqual(chart.history_hash(2, stamps), chart.history_hash(2, stamps[:1]))

    def test_graphql_error_does_not_echo_secrets(self):
        with patch.object(chart.urllib.request, 'urlopen', return_value=io.BytesIO(
                b'{"errors":[{"message":"private-test-marker"}]}')):
            with self.assertRaisesRegex(RuntimeError, '^GitHub returned a GraphQL error$'):
                chart.graphql('private-test-marker', {})
        self.assertNotRegex(chart.QUERY, r'\b(?:login|avatarUrl|url)\b')
