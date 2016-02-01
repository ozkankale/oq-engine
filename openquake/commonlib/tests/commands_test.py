import os
import sys
import mock
import shutil
import tempfile
import unittest

import numpy

from openquake.commonlib.commands.info import info
from openquake.commonlib.commands.show import show
from openquake.commonlib.commands.show_attrs import show_attrs
from openquake.commonlib.commands.export import export
from openquake.commonlib.commands.reduce import reduce
from openquake.commonlib.commands.run import _run
from openquake.qa_tests_data.classical import case_1
from openquake.qa_tests_data.classical_risk import case_3
from openquake.qa_tests_data.scenario import case_4
from openquake.qa_tests_data.event_based import case_5

DATADIR = os.path.join(os.path.dirname(__file__), 'data')


class Print(object):
    def __init__(self):
        self.lst = []

    def __call__(self, *args):
        self.lst.append(' '.join(map(unicode, args)))

    def __str__(self):
        return u'\n'.join(self.lst).encode('utf-8')

    @classmethod
    def patch(cls):
        bprint = 'builtins.print' if sys.version > '3' else '__builtin__.print'
        return mock.patch(bprint, cls())


class InfoTestCase(unittest.TestCase):
    EXPECTED = '''<CompositionInfo
b1, x15.xml, trt=[0], weight=1.00: 1 realization(s)>
See https://github.com/gem/oq-risklib/blob/master/doc/effective-realizations.rst for an explanation
<RlzsAssoc(1)
0,AkkarBommer2010: ['<0,b1,@_AkkarBommer2010_@_@_@_@_@,w=1.0>']>'''

    def test_zip(self):
        path = os.path.join(DATADIR, 'frenchbug.zip')
        with Print.patch() as p:
            info(path)
        self.assertEqual(self.EXPECTED, str(p))


class RunShowExportTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        """
        Build a datastore instance to show what it is inside
        """
        job_ini = os.path.join(os.path.dirname(case_1.__file__), 'job.ini')
        with Print.patch() as cls.p:
            cls.datastore = _run(job_ini, 0, False, 'info', None, '').datastore

    def test_run_calc(self):
        self.assertIn('See the output with hdfview', str(self.p))

    def test_show_calc(self):
        # test show all
        with Print.patch() as p:
            show(0)
        with Print.patch() as p:
            show(self.datastore.calc_id)
        self.assertIn('sitemesh', str(p))

        with Print.patch() as p:
            show(self.datastore.calc_id, 'sitemesh')
        self.assertEqual(str(p), '''\
lon,lat
0.00000000E+00,0.00000000E+00''')

    def test_show_attrs(self):
        with Print.patch() as p:
            show_attrs(self.datastore.calc_id, 'hcurve')
        self.assertEqual("'hcurve' is not in %s" % self.datastore, str(p))

        with Print.patch() as p:
            self.datastore['one'] = numpy.array([1])
            show_attrs(self.datastore.calc_id, 'one')
        self.assertEqual('one has no attributes', str(p))

        with Print.patch() as p:
            show_attrs(self.datastore.calc_id, 'hcurves')
        self.assertEqual("imtls [['PGA' '3']\n ['SA(0.1)' '3']]\nnbytes 48",
                         str(p))

    def test_export_calc(self):
        tempdir = tempfile.mkdtemp()
        with Print.patch() as p:
            export(self.datastore.calc_id, 'hcurves', export_dir=tempdir)
        [fname] = os.listdir(tempdir)
        self.assertIn(str(fname), str(p))
        shutil.rmtree(tempdir)


class ReduceTestCase(unittest.TestCase):
    TESTDIR = os.path.dirname(case_3.__file__)

    def test_exposure(self):
        tempdir = tempfile.mkdtemp()
        dest = os.path.join(tempdir, 'exposure_model.xml')
        shutil.copy(os.path.join(self.TESTDIR, 'exposure_model.xml'), dest)
        with Print.patch() as p:
            reduce(dest, 0.5)
        self.assertIn('Extracted 8 nodes out of 13', str(p))
        shutil.rmtree(tempdir)

    def test_source_model(self):
        tempdir = tempfile.mkdtemp()
        dest = os.path.join(tempdir, 'source_model.xml')
        shutil.copy(os.path.join(self.TESTDIR, 'source_model.xml'), tempdir)
        with Print.patch() as p:
            reduce(dest, 0.5)
        self.assertIn('Extracted 9 nodes out of 15', str(p))
        shutil.rmtree(tempdir)

    def test_site_model(self):
        testdir = os.path.dirname(case_4.__file__)
        tempdir = tempfile.mkdtemp()
        dest = os.path.join(tempdir, 'site_model.xml')
        shutil.copy(os.path.join(testdir, 'site_model.xml'), tempdir)
        with Print.patch() as p:
            reduce(dest, 0.5)
        self.assertIn('Extracted 2 nodes out of 3', str(p))
        shutil.rmtree(tempdir)

    def test_sites_csv(self):
        testdir = os.path.dirname(case_5.__file__)
        tempdir = tempfile.mkdtemp()
        dest = os.path.join(tempdir, 'sites.csv')
        shutil.copy(os.path.join(testdir, 'sites.csv'), tempdir)
        with Print.patch() as p:
            reduce(dest, 0.5)
        self.assertIn('Extracted 50 lines out of 100', str(p))
        shutil.rmtree(tempdir)
