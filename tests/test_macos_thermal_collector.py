#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
COLLECTOR_PATH = ROOT / "src" / "fleet_node_observability" / "commands" / "collect_macos_thermal.py"


def load_module():
    spec = importlib.util.spec_from_file_location("collect_macos_thermal", COLLECTOR_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load {COLLECTOR_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["collect_macos_thermal"] = module
    spec.loader.exec_module(module)
    return module


class MacosThermalCollectorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_module()

    def test_parse_known_thermal_levels(self) -> None:
        self.assertEqual(
            self.module.parse_thermal_level("CPU_Scheduler_Limit = 100\nThermal Pressure: Nominal"),
            (0, "nominal"),
        )
        self.assertEqual(self.module.parse_thermal_level("Thermal Pressure: Serious"), (2, "serious"))
        self.assertEqual(self.module.parse_thermal_level("Thermal Pressure: Critical"), (3, "critical"))
        self.assertEqual(
            self.module.parse_thermal_level("Note: No thermal warning level has been recorded"),
            (0, "nominal"),
        )

    def test_render_successful_pmset_output(self) -> None:
        result = subprocess.CompletedProcess(["pmset"], 0, "Thermal Pressure: Serious\n", "")
        with mock.patch.object(self.module.subprocess, "run", return_value=result):
            text = self.module.render("bill", "pmset", now=123)
        self.assertIn('fleet_macos_thermal_pressure_available{node="bill",node_label="bill",source="pmset"} 1', text)
        self.assertIn('fleet_macos_thermal_pressure_level{node="bill",node_label="bill",source="pmset",pressure="serious"} 2', text)
        self.assertIn('fleet_macos_thermal_collector_success{node="bill",node_label="bill",source="pmset"} 1', text)

    def test_render_sensorless_or_failed_collection_is_available_zero(self) -> None:
        result = subprocess.CompletedProcess(["pmset"], 1, "", "unsupported")
        with mock.patch.object(self.module.subprocess, "run", return_value=result):
            text = self.module.render("sensorless", "pmset", now=123)
        self.assertIn('fleet_macos_thermal_pressure_available{node="sensorless",node_label="sensorless",source="pmset"} 0', text)
        self.assertIn('fleet_macos_thermal_collector_success{node="sensorless",node_label="sensorless",source="pmset"} 0', text)
        self.assertIn("fleet_macos_thermal_collection_error_info", text)


if __name__ == "__main__":
    unittest.main()
