from __future__ import annotations

import unittest

from fleet_node_observability.textfile import escape_label_value


class TextfileEscapingTests(unittest.TestCase):
    def test_escape_label_value_escapes_standard_sequences_and_strips_other_controls(self) -> None:
        value = 'node\\name"line\ncol\tret\rbel\x07del\x7fend'

        self.assertEqual(
            escape_label_value(value),
            'node\\\\name\\"line\\ncol\\tret\\rbeldelend',
        )


if __name__ == "__main__":
    unittest.main()
