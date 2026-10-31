from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class SettlingTests(unittest.TestCase):
    def test_controller_with_mock_telemetry(self):
        compiler = shutil.which("gcc")
        if not compiler:
            self.skipTest("Host GCC unavailable")
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            (temp / "main.h").write_text('#include <stdint.h>\nuint32_t HAL_GetTick(void);\n')
            binary = temp / "mppt_settling.exe"
            args = [compiler, "-std=c11", "-Wall", "-Wextra", "-Werror"]
            for include in [temp, *(root / "firmware" / p for p in [
                "Inc", "Inc/app", "Inc/app/control", "Inc/app/control/controllers",
                "Inc/app/control/control_helpers", "Src/app/control/controllers"])]:
                args += ["-I", str(include)]
            args += [str(root / p) for p in ["tools/tests/mppt_settling_harness.c",
                     "firmware/Src/app/control/control_helpers/pi.c",
                     "firmware/Src/app/control/control_helpers/perturb_observe.c"]]
            args += ["-lm", "-o", str(binary)]
            result = subprocess.run(args, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("MPPT settling checks passed", result.stdout)


if __name__ == "__main__":
    unittest.main()
