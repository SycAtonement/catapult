# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import print_function
from __future__ import division
from __future__ import absolute_import

import datetime

from dashboard.common import request_handler
from dashboard.models import alert_group
from google.appengine.ext import ndb

# Waiting 7 days to gather more potential alerts. Just choose a long
# enough time and all alerts arrive after archived shouldn't be silent
# merged.
_ALERT_GROUP_ACTIVE_WINDOW = datetime.timedelta(days=7)

# (2020-05-01) Only ~62% issues' alerts are triggered in one hour.
# But we don't want to wait all these long tail alerts finished.
# SELECT APPROX_QUANTILES(diff, 100) as percentiles
#
# FROM (
#   SELECT TIMESTAMP_DIFF(MAX(timestamp), MIN(timestamp), MINUTE) as diff
#   FROM chromeperf.chromeperf_dashboard_data.anomalies
#   WHERE 'Chromium Perf Sheriff' IN UNNEST(subscription_names)
#         AND bug_id IS NOT NULL AND timestamp > '2020-03-01'
#   GROUP BY bug_id
# )
_ALERT_GROUP_TRIAGE_DELAY = datetime.timedelta(hours=1)


class AlertGroupsHandler(request_handler.RequestHandler):
  """Create and Update AlertGroups.
  All active groups are fetched and updated in every iteration. Auto-Triage
  and Auto-Bisection are triggered based on configuration in matching
  subscriptions.

  If an anomaly is associated with a special group named Ungrouped, all
  missing groups related to this anomaly will be created. Newly created groups
  won't be updated until next iteration.

  Groups will be archived after a time window passes and in status:
  - Untriaged: Only improvements in the group or auto-triage not enabled.
  - Closed: Issue closed.
  """

  def get(self):
    groups = alert_group.AlertGroup.GetAll()
    now = datetime.datetime.utcnow()
    for group in groups:
      group.Update(now, _ALERT_GROUP_ACTIVE_WINDOW, _ALERT_GROUP_TRIAGE_DELAY)
    ndb.put_multi(groups)

    def FindGroup(group):
      for g in groups:
        if group.revision.IsOverlapping(g.revision):
          return g.key
      groups.append(group)
      return None

    ungrouped_list = alert_group.AlertGroup.Get('Ungrouped', None)
    if not ungrouped_list:
      alert_group.AlertGroup(name='Ungrouped', active=True).put()
      return
    ungrouped = ungrouped_list[0]
    ungrouped_anomalies = ndb.get_multi(ungrouped.anomalies)

    # Scan all ungrouped anomalies and create missing groups. This doesn't
    # mean their groups are not created so we still need to check if group
    # has been created. There are two cases:
    # 1. If multiple groups are related to an anomaly, maybe only part of
    # groups are not created.
    # 2. Groups may be created during the iteration.
    # Newly created groups won't be updated until next iteration.
    for anomaly_entity in ungrouped_anomalies:
      anomaly_entity.groups = [
          FindGroup(g) or g.put()
          for g in alert_group.AlertGroup.GenerateAllGroupsForAnomaly(
              anomaly_entity)
      ]
    ndb.put_multi(ungrouped_anomalies)
