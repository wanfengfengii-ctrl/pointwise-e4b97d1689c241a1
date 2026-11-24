"""Run this project's full unittest suite plus the spill business check.

The unittest discovery below is the project's original regression and must
always run.  After it passes, the contract sample from ``ARCHIVE_SPILL.md`` is
replayed end to end (local body refusal, rejected spill commit, lost spill
reply, registration during the wait, resume, stop-source claiming) and its
results are asserted.  Any failure exits nonzero.
"""
import sys
import tempfile
import unittest
from pathlib import Path


def run_regression():
    suite = unittest.defaultTestLoader.discover('tests', top_level_dir='.')
    if suite.countTestCases():
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    else:
        print('No tests discovered in tests/', file=sys.stderr)
        result = unittest.TestResult()
    for status, failed_cases in (
        ('FAIL', (test for test, _ in result.failures)),
        ('ERROR', (test for test, _ in result.errors)),
        ('UNEXPECTED_SUCCESS', result.unexpectedSuccesses),
    ):
        for test in failed_cases:
            print(f'{status} {test.id()}')
    successful = result.testsRun > 0 and result.wasSuccessful()
    print(f'TEST SUMMARY total={result.testsRun} failures={len(result.failures)} '
          f'errors={len(result.errors)} skipped={len(result.skipped)} '
          f'expected_failures={len(result.expectedFailures)} '
          f'unexpected_successes={len(result.unexpectedSuccesses)} '
          f'result={"PASS" if successful else "FAIL"}')
    return successful


def run_spill_business_check():
    from spill_demo import run_demo

    try:
        with tempfile.TemporaryDirectory() as directory:
            run_demo(Path(directory))
    except BaseException as error:  # noqa: BLE001 - report and fail the check
        print(f'SPILL BUSINESS CHECK result=FAIL {type(error).__name__}: {error}')
        return False
    print('SPILL BUSINESS CHECK result=PASS')
    return True


def main():
    regression_ok = run_regression()
    spill_ok = run_spill_business_check() if regression_ok else False
    return 0 if regression_ok and spill_ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
