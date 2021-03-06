from __future__ import division, absolute_import, print_function
from past.builtins import xrange

import fitsio
import numpy as np
import esutil

from .cluster import ClusterCatalog
from .background import Background
from .mask import HPMask
from .galaxy import GalaxyCatalog
from .cluster import Cluster
from .cluster import ClusterCatalog
from .depthmap import DepthMap
from .zlambda import Zlambda
from .zlambda import ZlambdaCorrectionPar
from .cluster_runner import ClusterRunner

class RunCatalog(ClusterRunner):
    """
    """

    def _additional_initialization(self, **kwargs):
        # This is the runmode and where we get the mask/radius config vars from
        self.runmode = 'percolation'
        self.read_zreds = False
        self.zreds_required = False
        self.filetype = 'lambda_chisq'

    def _more_setup(self, *args, **kwargs):
        # I think I name the args here?

        # read in catalog, etc
        print("Reading in catalog file...")
        self.cat = ClusterCatalog.from_catfile(self.config.catfile,
                                               zredstr=self.zredstr,
                                               config=self.config,
                                               bkg=self.bkg,
                                               cosmo=self.cosmo)

        # check if we need to generate mem_match_ids
        self._generate_mem_match_ids()

        self.do_percolation_masking = kwargs.pop('do_percolation_masking', False)
        self.maxiter = kwargs.pop('maxiter', 5)
        self.tol = kwargs.pop('tol', 0.005)
        self.converge_zlambda = kwargs.pop('converge_zlambda', False)

        self.do_lam_plusminus = True
        self.match_centers_to_galaxies = True
        self.record_members = True

        # this is the minimum luminosity to consider
        # this is here to speed up computations.
        #self.limlum = np.clip(self.config.lval_reference - 0.1, 0.01, None)

        # additional bits to do with percolation limlum here
        # if we want to save p's for very faint objects we need to compute
        # values for them even if they don't contribute to the richness

        if (self.config.percolation_memlum > 0.0 and
            self.config.percolation_memlum < self.config.lval_reference):
            if self.config.percolation_memlum < self.limlum:
                self.limlum = self.config.percolation_memlum

        if self.config.percolation_lmask > 0.0:
            if self.config.percolation_lmask < self.limlum:
                self.limlum = self.config.percolation_lmask

    def _process_cluster(self, cluster):
        # here is where the work on an individual cluster is done
        bad = False
        iteration = 0
        done = False

        maxmag = cluster.mstar - 2.5*np.log10(self.limlum)

        while iteration < self.maxiter and not done:
            # Check if we got here because of a bad failure
            if bad:
                done = True
                continue

            # check if totally masked (with arbitrary 0.7 cut)
            if (cluster.maskfrac > 0.7):
                bad = True
                done = True
                continue

            # index, = np.where(cluster.neighbors.refmag < maxmag)

            lam = cluster.calc_richness(self.mask)

            # kick out if ridiculously low
            if (lam < 3.0):
                bad = True
                done = True
                self._reset_bad_values(cluster)
                continue

            # Compute z_lambda
            zlam = Zlambda(cluster)
            z_lambda, z_lambda_e = zlam.calc_zlambda(cluster.redshift, self.mask,
                                                     calc_err=True, calcpz=True)

            if z_lambda < 0.0:
                # total failure
                bad = True
                done = True
                self._reset_bad_values(cluster)
                continue

            cluster.z_lambda = z_lambda
            cluster.z_lambda_e = z_lambda_e
            cluster.z_lambda_niter = zlam.niter
            cluster.pzbins = zlam.pzbins
            cluster.pz = zlam.pz

            if self.converge_zlambda:
                if (np.abs(cluster.redshift - cluster.z_lambda) < self.tol):
                    done = True

                cluster.redshift = cluster.z_lambda
            else:
                done = True

        if not bad and self.zlambda_corr is not None:
            self.zlambda_corr.apply_correction(cluster.Lambda,
                                               cluster.z_lambda, cluster.z_lambda_e,
                                               pzbins=cluster.pzbins, pzvals=cluster.pz)

        return bad

