# Copyright (c) 2010-2011, GEM Foundation.
#
# OpenQuake is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# only, as published by the Free Software Foundation.
#
# OpenQuake is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License version 3 for more details
# (a copy is included in the LICENSE file that accompanied this code).
#
# You should have received a copy of the GNU Lesser General Public License
# version 3 along with OpenQuake.  If not, see
# <http://www.gnu.org/licenses/lgpl-3.0.txt> for a copy of the LGPLv3 License.


# pylint: disable=W0232

""" Mixin for Classical PSHA Risk Calculation """

import geohash

from celery.exceptions import TimeoutError

from openquake import kvs
from openquake import logs

from openquake.db import models

from openquake.parser import vulnerability
from openquake.risk import common
from openquake.risk import classical_psha_based as cpsha_based
from openquake.shapes import Curve

from openquake.risk.common import  compute_loss_curve
from openquake.risk.job import general

from openquake.utils.general import unique

LOGGER = logs.LOG


class ClassicalPSHABasedMixin:
    """Mixin for Classical PSHA Based Risk Job"""

    @general.preload
    @general.output
    def execute(self):
        """ execute -- general mixin entry point """
        celery_tasks = []
        for block_id in self.blocks_keys:
            LOGGER.warn("starting task block, block_id = %s of %s"
                        % (block_id, len(self.blocks_keys)))
            celery_tasks.append(
                general.compute_risk.delay(self.job_id, block_id))

        # task compute_risk has return value 'True' (writes its results to
        # kvs).
        for task in celery_tasks:
            try:
                # TODO(chris): Figure out where to put that timeout.
                task.wait()
                if not task.successful():
                    raise Exception(task.result)

            except TimeoutError:
                # TODO(jmc): Cancel and respawn this task
                return

    def _get_db_curve(self, site):
        """Read hazard curve data from the DB"""
        gh = geohash.encode(site.latitude, site.longitude, precision=12)
        job = models.OqJob.objects.get(id=self.job_id)
        hc = models.HazardCurveData.objects.filter(
            hazard_curve__output__oq_job=job,
            hazard_curve__statistic_type='mean').extra(
            where=["ST_GeoHash(location, 12) = %s"], params=[gh]).get()

        return Curve(zip(job.oq_params.imls, hc.poes))

    def compute_risk(self, block_id, **kwargs):  # pylint: disable=W0613
        """This task computes risk for a block of sites. It requires to have
        pre-initialized in kvs:
         1) list of sites
         2) exposure portfolio (=assets)
         3) vulnerability

        """

        block = general.Block.from_kvs(block_id)

        #pylint: disable=W0201
        self.vuln_curves = \
                vulnerability.load_vuln_model_from_kvs(self.job_id)

        for point in block.grid(self.region):
            hazard_curve = self._get_db_curve(point.site)

            asset_key = kvs.tokens.asset_key(self.job_id,
                            point.row, point.column)
            for asset in kvs.get_list_json_decoded(asset_key):
                LOGGER.debug("processing asset %s" % (asset))

                loss_ratio_curve = self.compute_loss_ratio_curve(
                    point, asset, hazard_curve)

                if loss_ratio_curve:
                    loss_curve = self.compute_loss_curve(point,
                            loss_ratio_curve, asset)

                    for loss_poe in general.conditional_loss_poes(self.params):
                        self.compute_conditional_loss(point.column, point.row,
                                loss_curve, asset, loss_poe)

        return True


    def compute_conditional_loss(self, col, row, loss_curve, asset, loss_poe):
        """Compute the conditional loss for a loss curve and Probability of
        Exceedance (PoE)."""

        # dups in the curve have to be skipped
        loss_curve_without_dups = Curve(zip(unique(loss_curve.abscissae),
            unique(loss_curve.ordinates)))

        loss_conditional = common.compute_conditional_loss(
            loss_curve_without_dups, loss_poe)

        key = kvs.tokens.loss_key(
                self.job_id, row, col, asset["assetID"], loss_poe)

        LOGGER.debug("Conditional loss is %s, write to key %s" %
                (loss_conditional, key))

        kvs.set(key, loss_conditional)

    def compute_loss_curve(self, point, loss_ratio_curve, asset):
        """
        Computes the loss ratio and store it in kvs to provide
        data to the @output decorator which does the serialization
        in the RiskJobMixin, more details inside
        openquake.risk.job.general.RiskJobMixin -- for details see
        RiskJobMixin._write_output_for_block and the output decorator

        :param point: the point of the grid we want to compute
        :type point: :py:class:`openquake.shapes.GridPoint`
        :param loss_ratio_curve: the loss ratio curve
        :type loss_ratio_curve: :py:class `openquake.shapes.Curve`
        :param asset: the asset for which to compute the loss curve
        :type asset: :py:class:`dict` as provided by
               :py:class:`openquake.parser.exposure.ExposurePortfolioFile`
        """

        loss_curve = compute_loss_curve(loss_ratio_curve, asset['assetValue'])
        loss_key = kvs.tokens.loss_curve_key(self.job_id, point.row,
            point.column, asset['assetID'])

        kvs.set(loss_key, loss_curve.to_json())

        return loss_curve

    def compute_loss_ratio_curve(self, point, asset, hazard_curve):
        """ Computes the loss ratio curve and stores in kvs
            the curve itself

        :param point: the point of the grid we want to compute
        :type point: :py:class:`openquake.shapes.GridPoint`
        :param asset: the asset used to compute the loss curve
        :type asset: :py:class:`dict` as provided by
            :py:class:`openquake.parser.exposure.ExposurePortfolioFile`
        :param hazard_curve: the hazard curve used to compute the
            loss ratio curve
        :type hazard_curve: :py:class:`openquake.shapes.Curve`
        """

        # we get the vulnerability function related to the asset

        vuln_function_reference = asset["vulnerabilityFunctionReference"]
        vuln_function = self.vuln_curves.get(
            vuln_function_reference, None)

        if not vuln_function:
            LOGGER.error(
                "Unknown vulnerability function %s for asset %s"
                % (asset["vulnerabilityFunctionReference"],
                asset["assetID"]))

            return None

        loss_ratio_curve = cpsha_based.compute_loss_ratio_curve(
            vuln_function, hazard_curve)

        loss_ratio_key = kvs.tokens.loss_ratio_key(
            self.job_id, point.row, point.column, asset['assetID'])

        kvs.set(loss_ratio_key, loss_ratio_curve.to_json())

        return loss_ratio_curve

general.RiskJobMixin.register("Classical", ClassicalPSHABasedMixin)
