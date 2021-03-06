#!/usr/bin/env python
# Copyright (c) 2013 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Tests for module json_summary_combiner.py"""

import filecmp
import os
import shutil
import tempfile
import unittest

import json_summary_combiner


class TestJsonSummaryCombiner(unittest.TestCase):

  def setUp(self):
    self._test_data_dir = os.path.join(
        os.path.dirname(os.path.realpath(__file__)), 'test_data', 'combiner')
    self._actual_html_dir = tempfile.mkdtemp()
    self._absolute_url = 'http://dummy-link.foobar/'
    self._render_pictures_args = '--test1=test --test2=test --test3'
    self._nopatch_gpu = 'False'
    self._withpatch_gpu = 'True'

  def tearDown(self):
    shutil.rmtree(self._actual_html_dir)

  def test_CombineJsonSummaries_WithDifferences(self):
    slave_name_to_info = json_summary_combiner.CombineJsonSummaries(
        os.path.join(self._test_data_dir, 'differences'))
    for slave_name, slave_info in slave_name_to_info.items():
      slave_num = slave_name[-1]
      file_count = 0
      for file_info in slave_info.failed_files:
        file_count += 1
        self.assertEquals(file_info.file_name,
                          'file%s_%s.png' % (slave_name, file_count))
        self.assertEquals(file_info.skp_location,
                          'http://storage.cloud.google.com/dummy-bucket/skps'
                          '/%s/file%s_.skp' % (slave_name, slave_name))
        self.assertEquals(file_info.num_pixels_differing,
                          int('%s%s1' % (slave_num, file_count)))
        self.assertEquals(file_info.percent_pixels_differing,
                          int('%s%s2' % (slave_num, file_count)))
        self.assertEquals(file_info.max_diff_per_channel,
                          int('%s%s4' % (slave_num, file_count)))

      self.assertEquals(
          slave_info.skps_location,
          'gs://dummy-bucket/skps/%s' % slave_name)
      self.assertEquals(
          slave_info.files_location_nopatch,
          'gs://dummy-bucket/output-dir/%s/nopatch-images' % slave_name)
      self.assertEquals(
          slave_info.files_location_diffs,
          'gs://dummy-bucket/output-dir/%s/diffs' % slave_name)
      self.assertEquals(
          slave_info.files_location_whitediffs,
          'gs://dummy-bucket/output-dir/%s/whitediffs' % slave_name)

  def test_CombineJsonSummaries_NoDifferences(self):
    slave_name_to_info = json_summary_combiner.CombineJsonSummaries(
        os.path.join(self._test_data_dir, 'no_output'))
    self.assertEquals(slave_name_to_info, {})

  def _get_test_slave_name_to_info(self):
    slave_name_to_info = {
        'slave1': json_summary_combiner.SlaveInfo(
            slave_name='slave1',
            failed_files=[
                json_summary_combiner.FileInfo(
                    'fileslave1_1.png',
                    'http://storage.cloud.google.com/dummy-bucket/skps/slave1/'
                    'fileslave1_.skp',
                    111, 112, 114, 115),
                json_summary_combiner.FileInfo(
                    'fileslave1_2.png',
                    'http://storage.cloud.google.com/dummy-bucket/skps/slave1/'
                    'fileslave1_.skp',
                    121, 122, 124, 125)],
            skps_location='gs://dummy-bucket/skps/slave1',
            files_location_diffs='gs://dummy-bucket/slave1/diffs',
            files_location_whitediffs='gs://dummy-bucket/slave1/whitediffs',
            files_location_nopatch='gs://dummy-bucket/slave1/nopatch',
            files_location_withpatch='gs://dummy-bucket/slave1/withpatch'),
        'slave2': json_summary_combiner.SlaveInfo(
            slave_name='slave2',
            failed_files=[
                json_summary_combiner.FileInfo(
                    'fileslave2_1.png',
                    'http://storage.cloud.google.com/dummy-bucket/skps/slave2/'
                    'fileslave2_.skp',
                    211, 212, 214, 215)],
            skps_location='gs://dummy-bucket/skps/slave2',
            files_location_diffs='gs://dummy-bucket/slave2/diffs',
            files_location_whitediffs='gs://dummy-bucket/slave2/whitediffs',
            files_location_nopatch='gs://dummy-bucket/slave2/nopatch',
            files_location_withpatch='gs://dummy-bucket/slave2/withpatch'),
        'slave3': json_summary_combiner.SlaveInfo(
            slave_name='slave3',
            failed_files=[
                json_summary_combiner.FileInfo(
                    'fileslave3_1.png',
                    'http://storage.cloud.google.com/dummy-bucket/skps/slave3/'
                    'fileslave3_.skp',
                    311, 312, 314, 315),
                json_summary_combiner.FileInfo(
                    'fileslave3_2.png',
                    'http://storage.cloud.google.com/dummy-bucket/skps/slave3/'
                    'fileslave3_.skp',
                    321, 322, 324, 325),
                json_summary_combiner.FileInfo(
                    'fileslave3_3.png',
                    'http://storage.cloud.google.com/dummy-bucket/skps/slave3/'
                    'fileslave3_.skp',
                    331, 332, 334, 335),
                json_summary_combiner.FileInfo(
                    'fileslave3_4.png',
                    'http://storage.cloud.google.com/dummy-bucket/skps/slave3/'
                    'fileslave3_.skp',
                    341, 342, 344, 345)],
            skps_location='gs://dummy-bucket/skps/slave3',
            files_location_diffs='gs://dummy-bucket/slave3/diffs',
            files_location_whitediffs='gs://dummy-bucket/slave3/whitediffs',
            files_location_nopatch='gs://dummy-bucket/slave3/nopatch',
            files_location_withpatch='gs://dummy-bucket/slave3/withpatch')
    }
    return slave_name_to_info

  def test_OutputToHTML_WithDifferences_WithAbsoluteUrl(self):
    slave_name_to_info = self._get_test_slave_name_to_info()
    json_summary_combiner.OutputToHTML(
        slave_name_to_info=slave_name_to_info,
        output_html_dir=self._actual_html_dir,
        absolute_url=self._absolute_url,
        render_pictures_args=self._render_pictures_args,
        nopatch_gpu=self._nopatch_gpu,
        withpatch_gpu=self._withpatch_gpu)

    html_expected_dir = os.path.join(self._test_data_dir, 'html_outputs',
                                     'differences_with_url')
    for html_file in ('index.html', 'list_of_all_files.html',
                      'fileslave1_1.png.html', 'fileslave1_2.png.html',
                      'fileslave2_1.png.html', 'fileslave3_1.png.html',
                      'fileslave3_2.png.html', 'fileslave3_3.png.html',
                      'fileslave3_4.png.html'):
      self.assertTrue(
          filecmp.cmp(os.path.join(html_expected_dir, html_file),
                      os.path.join(self._actual_html_dir, html_file)))

  def test_OutputToHTML_WithDifferences_WithNoUrl(self):
    slave_name_to_info = self._get_test_slave_name_to_info()
    json_summary_combiner.OutputToHTML(
        slave_name_to_info=slave_name_to_info,
        output_html_dir=self._actual_html_dir,
        absolute_url='',
        render_pictures_args=self._render_pictures_args,
        nopatch_gpu=self._nopatch_gpu,
        withpatch_gpu=self._withpatch_gpu)

    html_expected_dir = os.path.join(self._test_data_dir, 'html_outputs',
                                     'differences_no_url')
    for html_file in ('index.html', 'list_of_all_files.html',
                      'fileslave1_1.png.html', 'fileslave1_2.png.html',
                      'fileslave2_1.png.html', 'fileslave3_1.png.html',
                      'fileslave3_2.png.html', 'fileslave3_3.png.html',
                      'fileslave3_4.png.html'):
      self.assertTrue(
          filecmp.cmp(os.path.join(html_expected_dir, html_file),
                      os.path.join(self._actual_html_dir, html_file)))

  def test_OutputToHTML_NoDifferences(self):
    json_summary_combiner.OutputToHTML(
        slave_name_to_info={},
        output_html_dir=self._actual_html_dir,
        absolute_url='',
        render_pictures_args=self._render_pictures_args,
        nopatch_gpu=self._nopatch_gpu,
        withpatch_gpu=self._withpatch_gpu)

    html_expected_dir = os.path.join(self._test_data_dir, 'html_outputs',
                                     'nodifferences')
    self.assertTrue(
        filecmp.cmp(os.path.join(html_expected_dir, 'index.html'),
                    os.path.join(self._actual_html_dir, 'index.html')))


if __name__ == '__main__':
  unittest.main()
