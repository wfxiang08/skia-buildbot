#!/usr/bin/env python
# Copyright (c) 2012 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""This module contains utilities related to Google Storage manipulations.

TODO(epoger): Replace this old gs_utils.py with a new one, within the common
repo, that uses google-api-python-client rather than the gsutil tool.
See http://skbug.com/2618 ('buildbot code: use google-api-python-client instead
of gsutil tool')
"""

import hashlib
import os
import posixpath
import re
import shutil
import tempfile
import time

from py.utils import shell_utils
from slave import slave_utils

import file_utils


DEFAULT_DEST_GSBASE = 'gs://chromium-skia-gm'
TIMESTAMP_STARTED_FILENAME = 'TIMESTAMP_LAST_UPLOAD_STARTED'
TIMESTAMP_COMPLETED_FILENAME = 'TIMESTAMP_LAST_UPLOAD_COMPLETED'
LAST_REBASELINED_BY_FILENAME = 'LAST_REBASELINED_BY'

FILES_CHUNK = 500
BUFSIZE = 64 * 1024

ETAG_REGEX = re.compile(r'ETag:\s*(\S+)')


def delete_storage_object(object_name):
  """Delete an object on Google Storage."""
  gsutil = slave_utils.GSUtilSetup()
  command = [gsutil]
  command.extend(['rm', '-R', object_name])
  print 'Running command: %s' % command
  shell_utils.run(command)


def upload_file(local_src_path, remote_dest_path, gs_acl='private',
                http_header_lines=None, only_if_modified=False):
  """Upload contents of a local file to Google Storage.

  params:
    local_src_path: path to file on local disk
    remote_dest_path: GS URL (gs://BUCKETNAME/PATH)
    gs_acl: which predefined ACL to apply to the file on Google Storage; see
        https://developers.google.com/storage/docs/accesscontrol#extension
    http_header_lines: a list of HTTP header strings to add, if any
    only_if_modified: if True, only upload the file if it would actually change
        the content on Google Storage (uploads the file if remote_dest_path
        does not exist, or if it exists but has different contents than
        local_src_path).  Note that this may take longer than just uploading the
        file without checking first, due to extra round-trips!

  TODO(epoger): Consider adding a do_compress parameter that would compress
  the file using gzip before upload, and add a "Content-Encoding:gzip" header
  so that HTTP downloads of the file would be unzipped automatically.
  See https://developers.google.com/storage/docs/gsutil/addlhelp/
              WorkingWithObjectMetadata#content-encoding
  """
  gsutil = slave_utils.GSUtilSetup()

  if only_if_modified:
    # Return early if we don't need to do the upload.
    command = [gsutil, 'ls', '-L', remote_dest_path]
    try:
      ls_output = shell_utils.run(command)
      matches = ETAG_REGEX.search(ls_output)
      if matches:
        # TODO(epoger): In my testing, this has always returned an MD5 hash
        # that is comparable to local_md5 below.  But from my reading of
        # https://developers.google.com/storage/docs/hashes-etags , this is
        # not something we can always rely on ("composite objects don't support
        # MD5 hashes"; I'm not sure if we ever encounter composite objects,
        # though).  It would be good for us to find a more reliable hash, but
        # I haven't found a way to get one out of gsutil yet.
        #
        # For now: if the remote_md5 is not found, or is computed in
        # such a way that is different from local_md5, then we will re-upload
        # the file even if it did not change.
        remote_md5 = matches.group(1)
        hasher = hashlib.md5()
        with open(local_src_path, 'rb') as filereader:
          while True:
            data = filereader.read(BUFSIZE)
            if not data:
              break
            hasher.update(data)
        local_md5 = hasher.hexdigest()
        if local_md5 == remote_md5:
          print ('local_src_path %s and remote_dest_path %s have same hash %s' %
                 (local_src_path, remote_dest_path, local_md5))
          return
    except shell_utils.CommandFailedException:
      # remote_dest_path probably does not exist. Go ahead and do the upload.
      pass

  command = [gsutil]
  if http_header_lines:
    for http_header_line in http_header_lines:
      command.extend(['-h', http_header_line])
  command.extend(['cp', '-a', gs_acl, local_src_path, remote_dest_path])
  print 'Running command: %s' % command
  shell_utils.run(command)


def upload_dir_contents(local_src_dir, remote_dest_dir, gs_acl='private',
                        http_header_lines=None):
  """Upload contents of a local directory to Google Storage.

  params:
    local_src_dir: directory on local disk to upload contents of
    remote_dest_dir: GS URL (gs://BUCKETNAME/PATH)
    gs_acl: which predefined ACL to apply to the files on Google Storage; see
        https://developers.google.com/storage/docs/accesscontrol#extension
    http_header_lines: a list of HTTP header strings to add, if any

  The copy operates as a "merge with overwrite": any files in src_dir will be
  "overlaid" on top of the existing content in dest_dir.  Existing files with
  the same names will be overwritten.

  We upload each file as a separate call to gsutil.  This takes longer than
  calling "gsutil -m cp -R <source> <dest>", which can perform the uploads in
  parallel... but in http://skbug.com/2618 ('The Case of the Missing
  Mandrills') we figured out that was silently failing in some cases!

  TODO(epoger): Use the google-api-python-client API, like we do in
  https://skia.googlesource.com/skia/+/master/tools/pyutils/gs_utils.py ,
  rather than calling out to the gsutil tool.  See http://skbug.com/2618

  TODO(epoger): Upload multiple files simultaneously to reduce latency.

  TODO(epoger): Add a "noclobber" mode that will not upload any files would
  overwrite existing files in Google Storage.

  TODO(epoger): Consider adding a do_compress parameter that would compress
  the file using gzip before upload, and add a "Content-Encoding:gzip" header
  so that HTTP downloads of the file would be unzipped automatically.
  See https://developers.google.com/storage/docs/gsutil/addlhelp/
              WorkingWithObjectMetadata#content-encoding
  """
  gsutil = slave_utils.GSUtilSetup()
  command = [gsutil]
  if http_header_lines:
    for http_header_line in http_header_lines:
      command.extend(['-h', http_header_line])
  command.extend(['cp', '-a', gs_acl])

  abs_local_src_dir = os.path.abspath(local_src_dir)
  for (abs_src_dirpath, _, filenames) in os.walk(abs_local_src_dir):
    if abs_src_dirpath == abs_local_src_dir:
      # This file is within local_src_dir; no need to add subdirs to
      # abs_dest_dirpath.
      abs_dest_dirpath = remote_dest_dir
    else:
      # This file is within a subdir, so add subdirs to abs_dest_dirpath.
      abs_dest_dirpath = posixpath.join(
          remote_dest_dir,
          _convert_to_posixpath(
              os.path.relpath(abs_src_dirpath, abs_local_src_dir)))
    for filename in sorted(filenames):
      abs_src_filepath = os.path.join(abs_src_dirpath, filename)
      abs_dest_filepath = posixpath.join(abs_dest_dirpath, filename)
      shell_utils.run(command + [abs_src_filepath, abs_dest_filepath])


def download_dir_contents(remote_src_dir, local_dest_dir, multi=True):
  """Download contents of a Google Storage directory to local disk.

  params:
    remote_src_dir: GS URL (gs://BUCKETNAME/PATH)
    local_dest_dir: directory on local disk to write the contents into
    multi: boolean; whether to perform the copy in multithreaded mode.

  The copy operates as a "merge with overwrite": any files in src_dir will be
  "overlaid" on top of the existing content in dest_dir.  Existing files with
  the same names will be overwritten.
  """
  gsutil = slave_utils.GSUtilSetup()
  command = [gsutil]
  if multi:
    command.append('-m')
  command.extend(['cp', '-R', remote_src_dir, local_dest_dir])
  print 'Running command: %s' % command
  shell_utils.run(command)


def copy_dir_contents(remote_src_dir, remote_dest_dir, gs_acl='private',
                      http_header_lines=None):
  """Copy contents of one Google Storage directory to another.

  params:
    remote_src_dir: source GS URL (gs://BUCKETNAME/PATH)
    remote_dest_dir: dest GS URL (gs://BUCKETNAME/PATH)
    gs_acl: which predefined ACL to apply to the new files; see
        https://developers.google.com/storage/docs/accesscontrol#extension
    http_header_lines: a list of HTTP header strings to add, if any

  The copy operates as a "merge with overwrite": any files in src_dir will be
  "overlaid" on top of the existing content in dest_dir.  Existing files with
  the same names will be overwritten.

  Performs the copy in multithreaded mode, in case there are a large number of
  files.
  """
  gsutil = slave_utils.GSUtilSetup()
  command = [gsutil, '-m']
  if http_header_lines:
    for http_header_line in http_header_lines:
      command.extend(['-h', http_header_line])
  command.extend(['cp', '-a', gs_acl, '-R', remote_src_dir, remote_dest_dir])
  print 'Running command: %s' % command
  shell_utils.run(command)


def move_storage_directory(src_dir, dest_dir):
  """Move a directory on Google Storage."""
  gsutil = slave_utils.GSUtilSetup()
  command = [gsutil]
  command.extend(['mv', '-p', src_dir, dest_dir])
  print 'Running command: %s' % command
  shell_utils.run(command)


def list_storage_directory(dest_gsbase, subdir):
  """List the contents of the specified Storage directory."""
  gsbase_subdir = posixpath.join(dest_gsbase, subdir)
  status, output_gsutil_ls = slave_utils.GSUtilListBucket(gsbase_subdir, [])
  if status != 0:
    raise Exception(
        'Could not list contents of %s in Google Storage!' % gsbase_subdir)

  gs_files = []
  for line in set(output_gsutil_ls.splitlines()):
    # Ignore lines with warnings and status messages.
    if line and line.startswith(gsbase_subdir) and line != gsbase_subdir:
      gs_files.append(line)
  return gs_files


def does_storage_object_exist(object_name):
  """Checks if an object exists on Google Storage.

  Returns True if it exists else returns False.
  """
  gsutil = slave_utils.GSUtilSetup()
  command = [gsutil]
  command.extend(['ls', object_name])
  print 'Running command: %s' % command
  try:
    shell_utils.run(command)
    return True
  except shell_utils.CommandFailedException:
    return False


def download_directory_contents_if_changed(gs_base, gs_relative_dir, local_dir):
  """Compares the TIMESTAMP_LAST_UPLOAD_COMPLETED and downloads if different.

  The goal of download_directory_contents_if_changed and
  upload_directory_contents_if_changed is to attempt to replicate directory
  level rsync functionality to the Google Storage directories we care about.
  """
  if _are_timestamps_equal(gs_base, gs_relative_dir, local_dir):
    print '\n\n=======Local directory is current=======\n\n'
  else:
    file_utils.create_clean_local_dir(local_dir)
    gs_source = posixpath.join(gs_base, gs_relative_dir, '*')
    slave_utils.GSUtilDownloadFile(src=gs_source, dst=local_dir)
    if not _are_timestamps_equal(gs_base, gs_relative_dir, local_dir):
      raise Exception('Failed to download from GS: %s' % gs_source)


def _get_chunks(seq, n):
  """Yield successive n-sized chunks from the specified sequence."""
  for i in xrange(0, len(seq), n):
    yield seq[i:i+n]


def delete_directory_contents(gs_base, gs_relative_dir, files_to_delete):
  """Deletes the specified files from the Google Storage Directory.

  Args:
    gs_base: str - The Google Storage base. Eg: gs://rmistry.
    gs_relative_dir: str - Relative directory to the Google Storage base.
    files_to_delete: Files that should be deleted from the Google Storage
        directory. The files are deleted one at a time. If files_to_delete is
        None or empty then all directory contents are deleted.
  """
  gs_dest = posixpath.join(gs_base, gs_relative_dir)
  if files_to_delete:
    for file_to_delete in files_to_delete:
      delete_storage_object(object_name=posixpath.join(gs_dest, file_to_delete))
  else:
    delete_storage_object(gs_dest)


def upload_directory_contents_if_changed(gs_base, gs_relative_dir, gs_acl,
                                         local_dir, force_upload=False,
                                         upload_chunks=False,
                                         files_to_upload=None):
  """Compares the TIMESTAMP_LAST_UPLOAD_COMPLETED and uploads if different.

  Args:
    gs_base: str - The Google Storage base. Eg: gs://rmistry.
    gs_relative_dir: str - Relative directory to the Google Storage base.
    gs_acl: str - ACL to use when uploading to Google Storage.
    local_dir: str - The local directory to upload.
    force_upload: bool - Whether upload should be done regardless of timestamps
        matching or not.
    upload_chunks: bool - Whether upload should be done in chunks or in a single
        command.
    files_to_upload: str seq - Specific files that should be uploaded, if not
        specified then all files in local_dir are uploaded. If upload_chunks is
        True then files will be uploaded in chunks else they will be uploaded
        one at a time. The Google Storage directory is not cleaned before upload
        if files_to_upload is specified.

  The goal of download_directory_contents_if_changed and
  upload_directory_contents_if_changed is to attempt to replicate directory
  level rsync functionality to the Google Storage directories we care about.

  Returns True if contents were uploaded, else returns False.
  """
  if not force_upload and _are_timestamps_equal(gs_base, gs_relative_dir,
                                                local_dir):
    print '\n\n=======Local directory is current=======\n\n'
    return False
  else:
    local_src = os.path.join(local_dir, '*')
    gs_dest = posixpath.join(gs_base, gs_relative_dir)
    timestamp_value = time.time()

    if not files_to_upload:
      print '\n\n=======Delete Storage directory before uploading=======\n\n'
      delete_storage_object(gs_dest)

    print '\n\n=======Writing new TIMESTAMP_LAST_UPLOAD_STARTED=======\n\n'
    write_timestamp_file(
        timestamp_file_name=TIMESTAMP_STARTED_FILENAME,
        timestamp_value=timestamp_value, gs_base=gs_base,
        gs_relative_dir=gs_relative_dir, local_dir=local_dir, gs_acl=gs_acl)

    if upload_chunks:
      if files_to_upload:
        local_files = [
            os.path.join(local_dir, local_file)
            for local_file in files_to_upload]
      else:
        local_files = [
            os.path.join(local_dir, local_file)
            for local_file in os.listdir(local_dir)]
      for files_chunk in _get_chunks(local_files, FILES_CHUNK):
        gsutil = slave_utils.GSUtilSetup()
        command = [gsutil, 'cp'] + files_chunk + [gs_dest]
        try:
          shell_utils.run(command)
        except shell_utils.CommandFailedException:
          raise Exception(
              'Could not upload the chunk to Google Storage! The chunk: %s'
              % files_chunk)
    else:
      if files_to_upload:
        for file_to_upload in files_to_upload:
          if slave_utils.GSUtilDownloadFile(
              src=os.path.join(local_dir, file_to_upload), dst=gs_dest) != 0:
            raise Exception(
                'Could not upload %s to Google Storage!' % file_to_upload)
      else:
        if slave_utils.GSUtilDownloadFile(src=local_src, dst=gs_dest) != 0:
          raise Exception('Could not upload %s to Google Storage!' % local_src)

    print '\n\n=======Writing new TIMESTAMP_LAST_UPLOAD_COMPLETED=======\n\n'
    write_timestamp_file(
        timestamp_file_name=TIMESTAMP_COMPLETED_FILENAME,
        timestamp_value=timestamp_value, gs_base=gs_base,
        gs_relative_dir=gs_relative_dir, local_dir=local_dir, gs_acl=gs_acl)
    return True


def _are_timestamps_equal(gs_base, gs_relative_dir, local_dir):
  """Compares the local TIMESTAMP with the TIMESTAMP from Google Storage."""

  local_timestamp_file = os.path.join(local_dir, TIMESTAMP_COMPLETED_FILENAME)
  # Make sure that the local TIMESTAMP file exists.
  if not os.path.exists(local_timestamp_file):
    return False

  # Get the timestamp file from Google Storage.
  src = posixpath.join(gs_base, gs_relative_dir, TIMESTAMP_COMPLETED_FILENAME)
  temp_file = tempfile.mkstemp()[1]
  slave_utils.GSUtilDownloadFile(src=src, dst=temp_file)

  local_file_obj = open(local_timestamp_file, 'r')
  storage_file_obj = open(temp_file, 'r')
  try:
    local_timestamp = local_file_obj.read().strip()
    storage_timestamp = storage_file_obj.read().strip()
    return local_timestamp == storage_timestamp
  finally:
    local_file_obj.close()
    storage_file_obj.close()


def read_timestamp_file(timestamp_file_name, gs_base, gs_relative_dir):
  """Reads the specified TIMESTAMP file from the specified GS dir.

  Returns 0 if the file is empty or does not exist.
  """
  src = posixpath.join(gs_base, gs_relative_dir, timestamp_file_name)
  temp_file = tempfile.mkstemp()[1]
  slave_utils.GSUtilDownloadFile(src=src, dst=temp_file)

  storage_file_obj = open(temp_file, 'r')
  try:
    timestamp_value = storage_file_obj.read().strip()
    return timestamp_value if timestamp_value else "0"
  finally:
    storage_file_obj.close()


def write_timestamp_file(timestamp_file_name, timestamp_value, gs_base=None,
                         gs_relative_dir=None, gs_acl=None, local_dir=None):
  """Adds a timestamp file to a Google Storage and/or a Local Directory.

  If gs_base, gs_relative_dir and gs_acl are provided then the timestamp is
  written to Google Storage. If local_dir is provided then the timestamp is
  written to a local directory.
  """
  timestamp_file = os.path.join(tempfile.gettempdir(), timestamp_file_name)
  f = open(timestamp_file, 'w')
  try:
    f.write(str(timestamp_value))
  finally:
    f.close()
  if local_dir:
    shutil.copyfile(timestamp_file,
                    os.path.join(local_dir, timestamp_file_name))
  if gs_base and gs_relative_dir and gs_acl:
    slave_utils.GSUtilCopyFile(filename=timestamp_file, gs_base=gs_base,
                               subdir=gs_relative_dir, gs_acl=gs_acl)


def _convert_to_posixpath(localpath):
  """Convert localpath to posix format."""
  if os.sep == '/':
    return localpath
  else:
    return '/'.join(localpath.split(os.sep))