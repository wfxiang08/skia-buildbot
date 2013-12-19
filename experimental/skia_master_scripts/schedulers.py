# Copyright (c) 2013 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.


"""Scheduler which supports dependency chaining."""


from twisted.internet import defer
from twisted.python import log
from buildbot import util
from buildbot.db import buildsets
from buildbot.process import properties
from buildbot.scheduler import Nightly
from buildbot.schedulers.basic import SingleBranchScheduler
from buildbot.schedulers import base, triggerable
from buildbot.status.results import SUCCESS, WARNINGS

import copy
import pending_buildsets


# Monkeypatch this functionality into the buildsets connector.
def BuildsetsConnectorComponentGetBuildsetsForSourceStamp(self, ssid):
  """Retrieve all Buildsets for the given Source Stamp.

  Args:
      ssid: ID of the Source Stamp in question.

  Returns:
      List of Buildset dicts associated with the given Source Stamp.
  """
  def thd(conn):
    bs_tbl = self.db.model.buildsets
    q = bs_tbl.select()
    q = q.where(bs_tbl.c.sourcestampid == ssid)
    res = conn.execute(q)
    return [ self._row2dict(row) for row in res.fetchall() ]
  return self.db.pool.do(thd)
buildsets.BuildsetsConnectorComponent.getBuildsetsForSourceStamp = \
    BuildsetsConnectorComponentGetBuildsetsForSourceStamp


class DependencyFailedError(Exception):
  pass


class DependencyChainScheduler(base.BaseScheduler):
  """Dependent-like scheduler which attempts to satisfy its own dependencies,
  rather than waiting for them to be satisfied."""

  compare_attrs = base.BaseScheduler.compare_attrs + ('dependencies',)
  props_from_dependencies = ['got_revision']

  def __init__(self, dependencies, name, builder_name, properties=None):
    """Initialize this DependencyChainScheduler.

    Args:
        dependencies: List of DependencyChainScheduler instances whose builds
            must be run before this DependencyChainScheduler's builds may run.
        name: Name of this DependencyChainScheduler.
        builder_name: Name of the (single) builder triggered by this Scheduler.
        properties: Optional dictionary of build properties which builds
            triggered by this Scheduler will inherit.
    """
    for dep in dependencies:
      assert isinstance(dep, DependencyChainScheduler)
    if not properties:
      properties = {}
    dependency_list = [d.name for d in dependencies]
    properties['dependencies'] = dependency_list
    base.BaseScheduler.__init__(self, name, [builder_name], properties)
    self.dependencies = dependencies

    self._buildset_addition_subscr = None
    self._buildset_completion_subscr = None
    self.properties.setProperty('dependencies', dependency_list, 'Scheduler')

    # Lock for controlling access to the pending buildsets DB table.
    self._pending_buildset_lock = defer.DeferredLock()

  @util.deferredLocked('_pending_buildset_lock')
  def _unmet_deps_for_source_stamp(self, ssid):
    """Get the unmet dependencies for the given Source Stamp.

    Args:
        ssid: ID of the Source Stamp.

    Returns:
        List of DependencyChainScheduler instances corresponding to the
            dependencies for the given Source Stamp that have not yet been met.

    Raises:
        DependencyFailedError if any of the dependencies have failed.
    """
    def _got_buildset_props(props, buildsets):
      """Callback which runs once the properties for a list of buildsets have
      been retrieved from the database. Determines whether a buildset already
      exists for the given Source Stamp and if not, calls
      _maybe_add_buildset_for_source_stamp to add the buildset or satisfy its
      dependencies.

      Args:
          props: List of tuples of the form: (bool, dict) where each dict
              contains the properties, including the scheduler name, for a
              buildset.
          buildsets: List of buildset dictionaries.
      """
      assert len(buildsets) == len(props)
      unmet_deps = {}
      for dep in self.dependencies:
        unmet_deps[dep.name] = dep
      for i in xrange(len(buildsets)):
        scheduler = props[i][1].get('scheduler', (None, None))[0]
        if (not scheduler) or (scheduler not in unmet_deps):
          continue
        if buildsets[i]['complete']:
          if buildsets[i]['results'] in (SUCCESS, WARNINGS):
            unmet_deps.pop(scheduler)
          else:
            raise DependencyFailedError('Dependency failed: %s' % buildsets[i])
      return [scheduler_instance for
              scheduler_name, scheduler_instance in unmet_deps.items()]

    def _got_buildsets(buildsets):
      """Callback which runs once a list of buildsets have been retrieved from
      the database. Gets the properties for each buildset from the database and
      chains a callback.

      Args:
          buildsets: List of buildset dictionaries.
      """
      dl = []
      for buildset in buildsets:
        dl.append(
            self.master.db.buildsets.getBuildsetProperties(buildset['bsid']))
      d = defer.DeferredList(dl)
      d.addCallback(_got_buildset_props, buildsets)
      return d

    d = self.master.db.buildsets.getBuildsetsForSourceStamp(ssid)
    d.addCallback(_got_buildsets)
    return d

  @util.deferredLocked('_pending_buildset_lock')
  def _cancel_pending_buildsets(self, unused_callback_param, ssid):
    """Cancel all pending Buildsets for the given Source Stamp.

    Args:
        unused_callback_param: Unused; placeholder for the return value of a
            function to which this function may be attached as a callback.
        ssid: ID of the Source Stamp whose Buildsets should be aborted.
    """
    return (self.master.skia_db.pending_buildsets
            .cancel_pending_buildsets(ssid, self.name))

  @util.deferredLocked('_pending_buildset_lock')
  def _actually_add_buildset_for_source_stamp(self, ssid, build_props):
    """Unconditionally add Buildsets for the given Source Stamp, removing the
    associated pending Buildsets from the pending Buildsets table.

    Args:
        ssid: ID of the Source Stamp to build.
        build_props: instance of properties.Properties; properties to add to the
           build.
    """
    def _added_buildsets(_unused_callback_result):
      return (self.master.skia_db.pending_buildsets
                  .cancel_pending_buildsets(ssid, self.name))

    def _got_pending(pending):
      dl = []
      for bs in pending:
        props = properties.Properties()
        props.updateFromProperties(build_props)
        props.update({'ssid': ssid}, 'Scheduler')
        props.update(bs['properties'] or {}, 'Scheduler')
        dl.append(
            base.BaseScheduler.addBuildsetForSourceStamp(self,
                ssid=ssid,
                reason=bs['reason'],
                external_idstring=bs['external_idstring'],
                properties=props))
      if not dl:
        return defer.succeed(None)
      d = defer.DeferredList(dl)
      d.addCallback(_added_buildsets)
      return d
    d = self.master.skia_db.pending_buildsets.get_pending_buildsets(ssid,
                                                                    self.name)
    d.addCallback(_got_pending)
    return d

  def _maybe_add_buildset_for_source_stamp(self, unused_callback_param, ssid,
                                           launch_dependencies, arguments,
                                           set_properties=None):
    """If all dependencies have been met for the given Source Stamp, add a build
    sets for each pending build for the given Source Stamp. If not, and if it
    was requested, launch the unmet dependencies for the given Source Stamp.

    Args:
        unused_callback_param: Unused; placeholder for the return value of a
            function to which this function may be attached as a callback.
        ssid: ID of a Source Stamp.
        launch_dependencies: Boolean; whether or not the dependencies for this
            DependencyChainScheduler should be triggered if they haven't yet
            been satisfied.
        arguments: Dictionary of arguments to be passed to
            addBuildsetForSourceStamp.
        set_properties: optional dictionary of build properties to be propagated
            from a dependency to the current buildset.
    """
    def _got_dependencies(unmet_dependencies):
      if unmet_dependencies:
        if launch_dependencies:
          dl = [dep.addBuildsetForSourceStamp(ssid, **arguments)
                for dep in unmet_dependencies]
          return defer.DeferredList(dl)
      else:
        # TODO(borenet): Actually get build properties from the dependencies.
        props = properties.Properties()
        if set_properties:
          props.update(set_properties, 'Dependency')
        return self._actually_add_buildset_for_source_stamp(ssid=ssid,
                                                            build_props=props)
    d = self._unmet_deps_for_source_stamp(ssid)
    d.addCallback(_got_dependencies)
    return d

  @util.deferredLocked('_pending_buildset_lock')
  def _maybe_add_pending_buildset_for_source_stamp(self, unused_callback_param,
                                                   ssid, arguments):
    """Add a Buildset to the pending Buildsets table if no pending or already-
    inserted Buildset exists.

    Args:
        unused_callback_param: Unused; placeholder for the return value of a
            function to which this function may be attached as a callback.
        ssid: ID of the Source Stamp.
        arguments: Dictionary of arguments to be passed to addBuildset.
    """
    def _got_pending_buildsets(pending):
      """Callback which runs once the list of pending Buildsets has been
      retrieved from the database. If there is no pending Buildset with an
      identical set of arguments, add a pending Buildset.

      Args:
          pending: List of dicts of Buildset arguments representing not-yet-
              inserted Buildsets.
      """
      for bs in pending:
        if (str(bs['reason']) == str(arguments.get('reason', '')) and
            bs['properties'] == arguments.get('properties', {})):
          return defer.succeed(None)
      d = (self.master.skia_db.pending_buildsets
           .add_pending_buildset(ssid, self.name,
                                 [d.name for d in self.dependencies],
                                 **arguments))
      return d

    def _got_buildset_props(props, buildsets):
      """Callback which runs once the properties for a list of buildsets have
      been retrieved from the database. Determines whether a buildset already
      exists for the given Source Stamp and if not, calls
      _maybe_add_buildset_for_source_stamp to add the buildset or satisfy its
      dependencies.

      Args:
          props: List of tuples of the form: (bool, dict) where each dict
              contains the properties, including the scheduler name, for a
              buildset.
          buildsets: List of buildset dictionaries.
      """
      assert len(buildsets) == len(props)
      for i in xrange(len(buildsets)):
        if props[i][1].get('scheduler', (None, None))[0] == self.name:
          # If there's already a Buildset, don't insert one.
          return defer.succeed(None)
      d = (self.master.skia_db.pending_buildsets
           .get_pending_buildsets(ssid, self.name))
      d.addCallback(_got_pending_buildsets)
      return d

    def _got_buildsets(buildsets):
      """Callback which runs once a list of buildsets have been retrieved from
      the database. Gets the properties for each buildset from the database and
      chains a callback.

      Args:
          buildsets: List of buildset dictionaries.
      """
      dl = []
      for buildset in buildsets:
        dl.append(
            self.master.db.buildsets.getBuildsetProperties(buildset['bsid']))
      d = defer.DeferredList(dl)
      d.addCallback(_got_buildset_props, buildsets)
      return d
    d = self.master.db.buildsets.getBuildsetsForSourceStamp(ssid)
    d.addCallback(_got_buildsets)
    return d

  def addBuildsetForSourceStamp(self, ssid, **arguments):
    """Attempt to add a buildset for the given Source Stamp.

    For other Schedulers this function just adds the buildset. Instead, this
    Scheduler adds an entry to the pending dictionary and then calls
    _maybe_add_buildset_for_source_stamp, which will launch any unmet
    dependencies or add the buildset.

    Args:
        ssid: ID of a Source Stamp.
        arguments: Dictionary of arguments to be passed to
            addBuildsetForSourceStamp.
    """
    d = self._maybe_add_pending_buildset_for_source_stamp(
        unused_callback_param=None, ssid=ssid, arguments=arguments)
    d.addCallback(self._maybe_add_buildset_for_source_stamp, ssid=ssid,
                  launch_dependencies=True, arguments=arguments)
    return d

  def startService(self):
    """Called when the DependencyChainScheduler starts running. Registers
    callback function for when buildsets are completed."""
    self._buildset_completion_subscr = \
        self.master.subscribeToBuildsetCompletions(
            self._check_completed_buildsets)
    # check for any buildsets completed before we started
    d = self._check_completed_buildsets(None, None)
    d.addErrback(log.err, 'while checking for completed buildsets in start')

  def stopService(self):
    """Called when the DependencyChainScheduler stops running. Unregisters all
    callback functions."""
    if self._buildset_completion_subscr:
      self._buildset_completion_subscr.unsubscribe()
    return defer.succeed(None)

  def _buildset_completed(self, bsid, result):
    """Callback function which runs when any buildset is completed on the
    current build master. Just runs the _check_completed_buildsets function to
    determine whether any action is necessary.

    Args:
        bsid: ID of the completed buildset.
        result: Result of the completed buildset. Possible values are defined in
            builbot.status.results.
    """
    d = self._check_completed_buildsets(bsid, result)
    d.addErrback(log.err, 'while checking for completed buildsets')

  def _check_completed_buildsets(self, bsid, result):
    """For each newly-completed buildset, determine whether the buildset
    satisfied a dependency for a Source Stamp. If so, run
    _maybe_add_buildset_for_source_stamp which will add a buildset for the
    source stamp if all of its dependencies have been satisfied.

    Args:
        bsid: ID of the completed buildset.
        result: Result of the completed buildset. Possible values are defined in
            builbot.status.results.
    """

    def _got_buildset_props(props, buildset):
      properties_to_pass = {}
      if props:
        for k, v in props.iteritems():
          if k in DependencyChainScheduler.props_from_dependencies:
            properties_to_pass[k] = v[0]
      return self._maybe_add_buildset_for_source_stamp(
            unused_callback_param=None,
            ssid=buildset['sourcestampid'],
            launch_dependencies=False,
            arguments=None,
            set_properties=properties_to_pass)

    def _got_buildset(buildset):
      if buildset:
        d = self.master.db.buildsets.getBuildsetProperties(buildset['bsid'])
        d.addCallback(_got_buildset_props, buildset)
        return d
      else:
        return defer.succeed(None)

    d = self.master.db.buildsets.getBuildset(bsid)
    d.addCallback(_got_buildset)
    return d


class PerCommitScheduler(SingleBranchScheduler):
  """Scheduler which triggers a set of schedulers on every commit."""
  def __init__(self, dependencies, *args, **kwargs):
    """Initialize the PerCommitScheduler.

    Args:
        dependencies: List of schedulers which should trigger on every commit.
    """
    SingleBranchScheduler.__init__(self, builderNames=[], *args, **kwargs)
    self._dependencies = dependencies

  def addBuildsetForSourceStamp(self, *args, **kwargs):
    """Override of SingleBranchScheduler.addBuildsetForSourceStamp. Rather than
    actually adding a Buildset, trigger another set of schedulers."""
    dl = []
    for dependency in self._dependencies:
      dl.append(dependency.addBuildsetForSourceStamp(*args, **kwargs))
    return defer.DeferredList(dl)


class NightlyScheduler(Nightly):
  """Scheduler which triggers a set of schedulers every night."""
  def __init__(self, dependencies, *args, **kwargs):
    """Initialize the NightlyScheduler.

    Args:
        dependencies: List of schedulers which should trigger every night.
    """
    Nightly.__init__(self, builderNames=[], *args, **kwargs)
    self._dependencies = dependencies

  def addBuildsetForSourceStamp(self, *args, **kwargs):
    """Override of Nightly.addBuildsetForSourceStamp. Rather than actually
    adding a Buildset, trigger another set of schedulers."""
    dl = []
    for dependency in self._dependencies:
      dl.append(dependency.addBuildsetForSourceStamp(*args, **kwargs))
    return defer.DeferredList(dl)
