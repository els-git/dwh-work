import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from dwh_api.secret_store import SecretStore
from dwh_api.secret_file_store import EncryptedFileSecretStore
from dwh_api.secret_windows_durable import WindowsDurableFileCreate, DurabilityNotConfirmed, _flush_directory_windows

REF = '144f7bd8-a493-4799-b73b-678d3227fe12'

class Tests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.TemporaryDirectory(dir=os.environ['DWH_I02_TEST_TEMP'])
        self.folder = Path(self.root.name)
        self.store = EncryptedFileSecretStore(crypto=SecretStore(b'a'*32), directory=self.folder)
    def tearDown(self): self.root.cleanup()
    def test_success_real_encrypted_file_with_injected_barrier(self):
        hits=[]
        digest=WindowsDurableFileCreate(self.store,_flush=lambda p:hits.append(p)).create_and_verify(secret_ref=REF,value='synthetic only')
        self.assertEqual(hits,[self.folder]);self.assertEqual(len(digest),64)
        self.assertNotIn(b'synthetic only',(self.folder/(REF+'.bin')).read_bytes())
        self.assertEqual(self.store.read(secret_ref=REF),'synthetic only')
    def test_flush_failure_denies(self):
        def fail(_): raise OSError('secret internal path')
        with self.assertRaises(DurabilityNotConfirmed) as cm:
            WindowsDurableFileCreate(self.store,_flush=fail).create_and_verify(secret_ref=REF,value='synthetic only')
        self.assertNotIn('secret internal path',str(cm.exception))
        self.assertTrue((self.folder/(REF+'.bin')).exists())
    def test_barrier_must_be_called(self):
        with self.assertRaises(DurabilityNotConfirmed):
            WindowsDurableFileCreate(self.store,_flush=lambda p: (_ for _ in ()).throw(RuntimeError())).create_and_verify(secret_ref=REF,value='a')
    def test_invalid_ref_no_file(self):
        with self.assertRaises(DurabilityNotConfirmed):WindowsDurableFileCreate(self.store,_flush=lambda _:None).create_and_verify(secret_ref='../x',value='a')
        self.assertFalse(list(self.folder.glob('*.bin')))
    def test_empty_value_denied(self):
        with self.assertRaises(DurabilityNotConfirmed):WindowsDurableFileCreate(self.store,_flush=lambda _:None).create_and_verify(secret_ref=REF,value='')
    def test_duplicate_denied(self):
        wrapped=WindowsDurableFileCreate(self.store,_flush=lambda _:None)
        wrapped.create_and_verify(secret_ref=REF,value='abc')
        with self.assertRaises(DurabilityNotConfirmed):wrapped.create_and_verify(secret_ref=REF,value='other')
        self.assertEqual(self.store.read(secret_ref=REF),'abc')
    def test_wrong_store_denied(self):
        with self.assertRaises(DurabilityNotConfirmed):WindowsDurableFileCreate(object())
    def test_noncallable_provider_denied(self):
        with self.assertRaises(DurabilityNotConfirmed):WindowsDurableFileCreate(self.store,_flush='bad')
    def test_not_active_publishing_interface(self):
        self.assertFalse(hasattr(WindowsDurableFileCreate(self.store,_flush=lambda _:None),'activate'))
    def test_nonwindows_native_flush_fails_closed(self):
        if os.name!='nt':
            with self.assertRaises(DurabilityNotConfirmed):_flush_directory_windows(self.folder)
    def test_real_windows_native_barrier_when_available(self):
        if os.name!='nt': self.skipTest('Windows-only native flush')
        digest=WindowsDurableFileCreate(self.store).create_and_verify(secret_ref=REF,value='synthetic only')
        self.assertEqual(len(digest),64)

if __name__=='__main__':unittest.main()
