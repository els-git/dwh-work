"""ETL run models accept object runs (OBJECT_LOAD, package 010) that have no source batch:
/api/etl/runs and /api/integrations/{id}/runs answered 500 (ResponseValidationError) on the PC."""

import unittest

from pydantic import TypeAdapter

from dwh_api.models import EtlJobRun, EtlRun


class SourceBatchOptionalTest(unittest.TestCase):
    def test_source_batch_run_id_may_be_null(self):
        for model in (EtlRun, EtlJobRun):
            with self.subTest(model=model.__name__):
                adapter = TypeAdapter(model.model_fields["source_batch_run_id"].annotation)
                self.assertIsNone(adapter.validate_python(None))
                self.assertEqual(adapter.validate_python(24), 24)


if __name__ == "__main__":
    unittest.main()
