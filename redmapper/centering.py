from __future__ import division, absolute_import, print_function
from past.builtins import xrange

import fitsio
import esutil
import numpy as np

from .utilities import gaussFunction
from .utilities import interpol

class Centering(object):
    """
    Class for computing cluster centers
    """

    def __init__(self, cluster, zlambda_corr=None):
        # Reference to the cluster; may need to copy
        self.cluster = cluster

        # And the zlambda_corr structure
        self.zlambda_corr = zlambda_corr

        # For convenience, make references to these structures
        self.zredstr = cluster.zredstr
        self.config = cluster.config
        self.cosmo = cluster.cosmo

        # Reset values
        self.ra = np.zeros(self.config.percolation_maxcen) - 400.0
        self.dec = np.zeros(self.config.percolation_maxcen) - 400.0
        self.ngood = 0
        self.index = np.zeros(self.config.percolation_maxcen, dtype=np.int32) - 1
        self.maxind = -1
        self.lnlamlike = -1.0
        self.lnbcglike = -1.0
        self.p_cen = np.zeros(self.config.percolation_maxcen)
        self.q_cen = np.zeros(self.config.percolation_maxcen)
        self.p_fg = np.zeros(self.config.percolation_maxcen)
        self.q_miss = 0.0
        self.p_sat = np.zeros(self.config.percolation_maxcen)
        self.p_c = np.zeros(self.config.percolation_maxcen)

    def find_center(self):
        return False

class CenteringBCG(Centering):

    def find_center(self):
        # This is somewhat arbitrary, and is not yet configurable
        pmem_cut = 0.8

        use, = np.where((self.cluster.neighbors.r < self.cluster.r_lambda) &
                        ((self.cluster.neighbors.pmem > pmem_cut) |
                         (np.abs(self.cluster.neighbors.zred - self.cluster.redshift) < 2.0 * self.cluster.neighbors.zred_e)))

        if use.size == 0:
            return False

        mind = np.argmin(self.cluster.neighbors.refmag[use])

        self.maxind = use[mind]
        self.ra = self.cluster.neighbors.ra[self.maxind]
        self.dec = self.cluster.neighbors.dec[self.maxind]
        self.ngood = 1
        self.index[0] = self.maxind
        self.p_cen[0] = 1.0
        self.q_cen[0] = 1.0
        self.p_sat[0] = 0.0

        return True

class CenteringWcenZred(Centering):

    def find_center(self):

        # These are the galaxies considered as candidate centers
        use, = np.where((self.cluster.neighbors.r < self.cluster.r_lambda) &
                        (self.cluster.neighbors.pfree >= self.config.percolation_pbcg_cut) &
                        (self.cluster.neighbors.zred_chisq < self.config.wcen_zred_chisq_max) &
                        ((self.cluster.neighbors.pmem > 0.0) |
                         (np.abs(self.cluster.redshift - self.cluster.neighbors.zred) < 5.0 * self.cluster.neighbors.zred_e)))

        # Do the phi_cen filter
        mbar = self.cluster.mstar + self.config.wcen_Delta0 + self.config.wcen_Delta1 * np.log(self.cluster.Lambda / self.config.wcen_pivot)
        phi_cen = gaussFunction(self.cluster.neighbors.refmag[use],
                                1. / (np.sqrt(2. * np.pi) * self.config.wcen_sigma_m),
                                mbar,
                                self.config.wcen_sigma_m)

        if self.zlambda_corr is not None:
            zrmod = interpol(self.zlambda_corr.zred_uncorr, self.zlambda_corr.z, self.cluster.redshift)
            gz = gaussFunction(self.cluster.neighbors.zred[use],
                               1. / (np.sqrt(2. * np.pi) * self.cluster.neighbors.zred_e[use]),
                               zrmod,
                               self.cluster.neighbors.zred_e[use])
        else:
            gz = gaussFunction(self.cluster.neighbors.zred[use],
                               1. / (np.sqrt(2. * np.pi) * self.cluster.neighbors.zred_e[use]),
                               self.cluster.redshift,
                               self.cluster.neighbors.zred_e[use])

        # and the w filter.  We need w for each galaxy that is considered a candidate center.
        # Note that in order to calculate w we need to know all the galaxies that are
        # around it, but only within r_lambda *of that galaxy*.  This is tricky.

        u, = np.where(self.cluster.neighbors.p > 0.0)

        maxrad = 1.1 * self.cluster.r_lambda / self.cluster.mpc_scale

        htm_matcher = esutil.htm.Matcher(self.cluster.neighbors.depth,
                                         self.cluster.neighbors.ra[use],
                                         self.cluster.neighbors.dec[use])
        i2, i1, dist = htm_matcher.match(self.cluster.neighbors.ra[u],
                                         self.cluster.neighbors.dec[u],
                                         maxrad, maxmatch=0)

        subdifferent, = np.where(~(use[i1] == u[i2]))
        i1 = i1[subdifferent]
        i2 = i2[subdifferent]
        pdis = dist[subdifferent] * self.cluster.mpc_scale
        pdis = np.sqrt(pdis**2. + self.config.wcen_rsoft**2.)

        lum = 10.**((self.cluster.mstar - self.cluster.neighbors.refmag) / (2.5))

        w = np.zeros(use.size)
        for i in xrange(use.size):
            # need to filter on r_lambda...
            subgal, = np.where(i1 == i)
            if subgal.size > 0:
                inside, = np.where(pdis[subgal] < self.cluster.r_lambda)
                if inside.size > 0:
                    indices = u[i2[subgal[inside]]]
                    if self.config.wcen_uselum:
                        w[i] = np.log(np.sum(self.cluster.neighbors.p[indices] * lum[indices] /
                                             pdis[subgal[inside]]) /
                                      ((1. / self.cluster.r_lambda) *
                                       np.sum(self.cluster.neighbors.p[indices] * lum[indices])))
                    else:
                        w[i] = np.log(np.sum(self.cluster.neighbors.p[indices] /
                                             pdis[subgal[inside]]) /
                                      ((1. / self.cluster.r_lambda) *
                                       np.sum(self.cluster.neighbors.p[indices])))

        sigscale = np.sqrt((np.clip(self.cluster.Lambda, None, self.config.wcen_maxlambda) / self.cluster.scaleval) / self.config.wcen_pivot)

        # scale with richness for Poisson errors
        sig = self.config.lnw_cen_sigma / sigscale

        fw = gaussFunction(np.log(w),
                           1. / (np.sqrt(2. * np.pi) * sig),
                           self.config.lnw_cen_mean,
                           sig)

        ucen = phi_cen * gz * fw

        lo, = np.where(ucen < 1e-10)
        ucen[lo] = 0.0

        # and the satellite function
        maxmag = self.cluster.mstar - 2.5 * np.log10(self.config.lval_reference)
        phi_sat = self.cluster._calc_luminosity(maxmag, idx=use)

        satsig = self.config.lnw_sat_sigma / sigscale
        fsat = gaussFunction(np.log(w),
                             1. / (np.sqrt(2. * np.pi) * satsig),
                             self.config.lnw_sat_mean,
                             satsig)

        usat = phi_sat * gz * fsat

        lo, = np.where(usat < 1e-10)
        usat[lo] = 0.0

        # and the background/foreground
        fgsig = self.config.lnw_fg_sigma / sigscale
        ffg = gaussFunction(np.log(w),
                            1. / (np.sqrt(2. * np.pi) * fgsig),
                            self.config.lnw_fg_mean,
                            fgsig)

        # we want to divide out the r, and we don't want small r's messing this up
        rtest = np.zeros(use.size) + 0.1

        bcounts = ffg * (self.cluster.calc_zred_bkg_density(rtest,
                                                            self.cluster.neighbors.zred[use],
                                                            self.cluster.neighbors.refmag[use]) /
                         (2. * np.pi * rtest)) * np.pi * self.cluster.r_lambda**2.

        # The start of Pcen
        Pcen_basic = np.clip(self.cluster.neighbors.pfree[use] * (ucen / (ucen + (self.cluster.Lambda / self.cluster.scaleval - 1.0) * usat + bcounts)),None, 0.99999)

        # make sure we don't have any bad values
        bad, = np.where(~np.isfinite(Pcen_basic))
        Pcen_basic[bad] = 0.0

        okay, = np.where(Pcen_basic > 0.0)
        if okay.size == 0:
            # There are literally NO centers
            self.q_miss = 1.0

            # Set the same as the input galaxy...
            good = np.argmin(self.cluster.neighbors.r[use])

            maxind = use[good]

            Pcen = np.zeros(use.size)
            Qcen = np.zeros(use.size)

        else:
            # Do the renormalization

            Pcen_unnorm = np.zeros(use.size)

            st = np.argsort(Pcen_basic)[::-1]
            if st.size < self.config.percolation_maxcen:
                good = st
            else:
                good = st[0: self.config.percolation_maxcen]

            self.ngood = good.size

            for i in xrange(self.ngood):
                Pcen0 = Pcen_basic[good[i]]
                Pcen_basic[good[i]] = 0.0
                Pcen_unnorm[good[i]] = Pcen0 * np.prod(1.0 - Pcen_basic[good])
                Pcen_basic[good[i]] = Pcen0

            Qmiss = np.prod(1.0 - Pcen_basic[good])

            KQ = 1./(Qmiss + np.sum(Pcen_unnorm))
            KP = 1./np.sum(Pcen_unnorm)

            Pcen = KP * Pcen_unnorm
            Qcen = KQ * Pcen_unnorm

            mod1 = np.sum(np.log(ucen[good] + (self.cluster.Lambda - 1) * usat[good] + bcounts[good]))
            mod2 = np.sum(np.log(self.cluster.Lambda * usat[good] + bcounts[good]))

            # A new statistic that doesn't quite work
            Qmiss = -2.0 * np.sum(np.log((ucen[good] + (self.cluster.Lambda - 1) * usat[good] + bcounts[good]) / (self.cluster.Lambda * usat[good] + bcounts[good])))

            maxind = use[good[0]]

        Pfg_basic = bcounts[good] / ((self.cluster.Lambda - 1.0) * usat[good] + bcounts[good])
        inf, = np.where(~np.isfinite(Pfg_basic))
        Pfg_basic[inf] = 0.0

        Pfg = (1.0 - Pcen[good]) * Pfg_basic

        Psat_basic = (self.cluster.Lambda - 1.0) * usat[good] / ((self.cluster.Lambda - 1.0) * usat[good] + bcounts[good])
        inf, = np.where(~np.isfinite(Psat_basic))
        Psat_basic[inf] = 0.0

        Psat = (1.0 - Pcen[good]) * Psat_basic

        self.ra[0: good.size] = self.cluster.neighbors.ra[use[good]]
        self.dec[0: good.size] = self.cluster.neighbors.dec[use[good]]
        self.index[0: good.size] = use[good]
        self.p_cen[0: good.size] = Pcen[good]
        self.q_cen[0: good.size] = Qcen[good]
        self.p_fg[0: good.size] = Pfg
        self.p_sat[0: good.size] = Psat
        self.p_c[0: good.size] = Pcen_basic[good]

        return True

