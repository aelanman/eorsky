#!/bin/env python

#SBATCH -J eorsky
#SBATCH -t 12:00:00
#SBATCH -n 1
#SBATCH --cpus-per-task=10
#SBATCH --mem=50G


"""
Calculate visibilities for:
    > Gaussian beam
    > Single baseline
    > Sky from file or generated on the fly

and save to MIRIAD file
"""

import numpy as np
from eorsky import visibility, utils
import pylab as pl
from scipy.stats import binned_statistic
import os, sys, yaml
from pyuvsim.simsetup import check_file_exists_and_increment
from pyuvdata import UVData
from pyuvdata import utils as uvutils
from eorsky import comoving_voxel_volume
import argparse

parser = argparse.ArgumentParser()

parser.add_argument('-w', '--beam_width', dest='fwhm', help='Primary gaussian beam fwhm, in degrees', default=50, type=float)
parser.add_argument('--fov', dest='fov', help='Field of view, in degrees', default=100, type=float)
parser.add_argument('-s' , '--sigma', dest='sigma', help='Sky sigma', default=2.0, type=float)
parser.add_argument('--Nside', dest='Nside', help='Sky resolution Nside', default=128, type=int)
parser.add_argument('-t', '--Ntimes', dest='Ntimes', help='Number of 11sec integration times, default is 24 hours\' worth', default=7854, type=int)
parser.add_argument('-b', '--baseline_length', dest='bllen', help='Baseline length in meters', default=14.6, type=float)
parser.add_argument('--Nskies', dest='Nskies', help='Number of sky realizations to generate.', default=1, type=int)
parser.add_argument('-N', dest='Nprocs', help='Number of processors.', default=1)

args = parser.parse_args()

if 'SLURM_CPUS_PER_TASK' in os.environ:
    Nprocs = int(os.environ['SLURM_CPUS_PER_TASK'])
else:
    Nprocs = int(args.Nprocs)
# Observatory
latitude  = -30.7215277777
longitude =  21.4283055554
altitude = 1073.
fov = args.fov  #Deg
ant1_enu = np.array([0, 0, 0])
ant2_enu = np.array([0.0, args.bllen , 0])
bl = visibility.baseline(ant1_enu, ant2_enu)

# Time

t0 = 2451545.0      #Start at J2000 epoch
#Ntimes = 7854  # 24 hours in 11 sec chunks
Nfreqs = 384
Ntimes = args.Ntimes
#Ntimes = 500
time_arr = np.linspace(t0, t0 + Ntimes/float(3600. * 24 / 11.), Ntimes)

# Frequency
freqs  = np.linspace(1e8, 1.3e8, Nfreqs)  #30 MHz
Nfreqs = freqs.size

# Shells
Nside = args.Nside
Npix = 12*Nside**2
sig = args.sigma
Nskies = args.Nskies
#shell0 = np.zeros((Nskies,Npix,Nfreqs), dtype=float)
shell0 = utils.mparray((Nskies, Npix, Nfreqs), dtype=float)
#shell0 = np.random.normal(0.0, sig, (Nskies, Npix, Nfreqs))
dnu = np.diff(freqs)[0]/1e6
om = 4*np.pi/float(Npix)
Zs = 1420e6/freqs - 1
dV0 = comoving_voxel_volume(Zs[Nfreqs/2], dnu, om)
print("Making skies")
for fi in range(Nfreqs):
    dV = comoving_voxel_volume(Zs[fi], dnu, om) 
    s = sig  * np.sqrt(dV0/dV)
    shell0[:,:,fi] = np.random.normal(0.0, s, (Nskies, Npix))

#Make observatories
visibs = []
#fwhms = [2.5, 5.0, 10.0, 20.0, 25.0, 30.0]
#fwhms = [35, 40, 45.0, 50.0, 55.0, 60.0]
fwhm = args.fwhm
sigma = fwhm/2.355
obs = visibility.observatory(latitude, longitude, array=[bl], freqs=freqs)
obs.set_fov(fov)
obs.set_pointings(time_arr)
obs.set_beam('gaussian', sigma=sigma)
print("Nprocs: ", Nprocs)
print("Shell = {:.4f}MB".format(shell0.nbytes/1e6))
visibs.append(obs.make_visibilities(shell0, Nprocs=Nprocs)[0])
# Visibilities are in Jy

# Get beam_sq_int
za, az = obs.calc_azza(Nside, obs.pointing_centers[0])
beam_sq_int = np.sum(obs.beam.beam_val(az, za)**2)
beam_sq_int = np.ones(Nfreqs) * beam_sq_int * om 


uv = UVData()
uv.Nbls = 1
uv.Ntimes = Ntimes
uv.spw_array = [0]
uv.Nfreqs = Nfreqs
uv.freq_array = freqs[np.newaxis, :]
uv.Nblts = uv.Ntimes * uv.Nbls
uv.ant_1_array = np.zeros(uv.Nblts, dtype=int)
uv.ant_2_array = np.ones(uv.Nblts, dtype=int)
uv.baseline_array = uv.antnums_to_baseline(uv.ant_1_array, uv.ant_2_array)
uv.time_array = time_arr
uv.Npols = 1
uv.polarization_array=np.array([1])
uv.Nants_telescope = 2
uv.Nants_data = 2
uv.antenna_positions = uvutils.ECEF_from_ENU(np.stack([ant1_enu, ant2_enu]), latitude, longitude, altitude)
uv.Nspws = 1
uv.antenna_numbers = np.array([0,1])
uv.antenna_names = ['ant0', 'ant1']
#uv.channel_width = np.ones(uv.Nblts) * np.diff(freqs)[0]
uv.channel_width = np.diff(freqs)[0]
uv.integration_time = np.ones(uv.Nblts) * np.diff(time_arr)[0] * 24 * 3600.  # Seconds
uv.uvw_array = np.tile(ant1_enu - ant2_enu, uv.Nblts).reshape(uv.Nblts, 3)
uv.history = 'Eorsky simulated'
uv.set_drift()
uv.telescope_name = 'Eorsky gaussian'
uv.instrument = 'simulator'
uv.object_name = 'zenith'
uv.vis_units = 'Jy'
uv.telescope_location_lat_lon_alt_degrees = (latitude, longitude, altitude)
uv.set_lsts_from_time_array()
uv.extra_keywords = {'bsq_int': beam_sq_int[0], 'skysig': sig, 'bm_fwhm' : fwhm, 'nside': Nside}

for sky_i in range(Nskies):
    if Nskies > 1:
        ofilename = 'eorsky_gauss{}d_{:.2f}hours_{}m_{}nside_{}fov_{}sky_uv'.format(args.fwhm, args.Ntimes/(3600./11.0), args.bllen, args.Nside, args.fov, sky_i)
    else:
        ofilename = 'eorsky_gauss{}d_{:.2f}hours_{}m_{}nside_{}fov_uv'.format(args.fwhm, args.Ntimes/(3600./11.0), args.bllen, args.Nside, args.fov, sky_i)
    print("ofilename: ", ofilename)
    data_arr = visibs[0][:,sky_i,:]  # (Nblts, Nskies, Nfreqs)
    data_arr = data_arr[:,np.newaxis,:,np.newaxis]  # (Nblts, Nspws, Nfreqs, Npol)
    uv.data_array = data_arr

    uv.flag_array = np.zeros(uv.data_array.shape).astype(bool)
    uv.nsample_array = np.ones(uv.data_array.shape).astype(float)

    uv.check()
    uv.write_miriad(ofilename, clobber=True)
