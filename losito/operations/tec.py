#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
TEC operation for losito: generates dTEC corruptions
"""
import warnings, os
import logging as log
import multiprocessing as mp
import numpy as np
from astropy import units as u
from astropy import wcs
from astropy.io import fits as pyfits
from astropy.time import Time
import astropy.coordinates as coord
from astropy.coordinates import EarthLocation, AltAz
from astropy.utils.exceptions import AstropyWarning
from losoto.h5parm import h5parm
import RMextract.PosTools as post
from ..lib_tecscreen import comoving_tecscreen, daytime_tec_modulation

log.debug('Loading TEC module.')
# Mute AP warnings for now... 
warnings.simplefilter('ignore', category=AstropyWarning)

def _run_parser(obs, parser, step):
    method = parser.getstr(step, 'method', default = 'turbulence')
    h5parmFilename = parser.getstr(step, 'h5parmFilename', )
    maxdtec = parser.getfloat(step, 'maxdtec', default = .5)
    maxvtec = parser.getfloat(step, 'maxvtec', default = 50.)
    hIon = parser.getfloat(step, 'hIon', default = 200e3)
    vIon= parser.getfloat(step, 'hIon', default = 50)
    seed = parser.getint(step, 'seed', default = 0)
    fitsFilename = parser.getstr(step, 'fitsFilename', default = '')
    absoluteTEC = parser.getbool(step, 'absoluteTEC', default = True)
    angRes = parser.getfloat(step, 'angRes', default = 60.)
    ncpu = parser.getint( '_global', 'ncpu', 0)
       
    parser.checkSpelling( step, ['method', 'h5parmFilename', 'maxdtec',
                                 'maxvtec', 'hIon', 'vIon', 'seed', 
                                 'fitsFilename', 'absoluteTEC', 'angRes', 
                                 'ncpu'])  
    return run(obs, method, h5parmFilename, maxdtec, maxvtec, hIon, vIon, seed, 
               fitsFilename, step, absoluteTEC, angRes, ncpu)

def _getaltaz(radec):
    ra = radec[0]
    dec = radec[1]
    aa = radec[2]
    
    mycoord = coord.SkyCoord(ra, dec, frame=coord.FK5, unit=(u.hourangle, u.deg))
    mycoord_aa = mycoord.transform_to(aa)
    return mycoord_aa

def _gettec(altaz_args):
    alltec = []
    altaz, stationpositions, A12, times, tidAmp, tidLen, tidVel = altaz_args
    direction = altaz.geocentrictrueecliptic.cartesian.xyz.value
    for ant in stationpositions:
        pp, am = post.getPPsimple([200.e3]*direction[0].shape[0], ant, direction)
        ppa = EarthLocation.from_geocentric(pp[:, 0], pp[:, 1], pp[:, 2], unit=u.m)
        ppaproj = EarthLocation.from_geodetic(-ppa.lon.deg+A12.lon.deg, -ppa.lat.deg+A12.lat.deg, ppa.height)
        x = ppaproj.z.value
        y = ppaproj.y.value
        tec = _tid(x, times*3600.*24, tidAmp, tidLen, tidVel)
        alltec.append([tec, x, y, altaz.secz])
    return alltec

def _tid(x, t, amp=0.2, wavelength=200e3, omega=500.e3/3600.):
    return amp*np.sin((x+omega*t)*2*np.pi/wavelength)


def run(obs, method, h5parmFilename, maxdtec = 0.5, maxvtec = 50, hIon = 200e3,
        vIon = 50, seed = None, fitsFilename = None, stepname='tec', 
        absoluteTEC = True, angRes = 60, ncpu=0):
    """
    Creates h5parm with TEC values from TEC FITS cube.

    Parameters
    ----------
    method : str
        Method to use:
        "fits": read TEC values from the FITS cube specified by fitsFilename
        "tid": generate a traveling ionospheric disturbance (TID) wave
    h5parmFilename : str
        Filename of output h5parm file.
    maxdtec : float, optional. Default = 0.5
        Maximum screen dTEC per timestep in TECU.
    maxvtec: float, optional. Default = 50.
        Highest vTEC in daily modulation in TECU.
    hIon : float, optional. Default = 200 km
        Height of thin layer ionoshpere.
    vIono : float, optional. Default = 50 m/s
        Velocity of tecscreen. This controls the tec variation frequency.
    seed: int, optional.
        Radnom screen seed. Use for reproducibility.
    fitsFilename : str, optional
        Filename of input FITS cube with dTEC solutions.
    stepname _ str, optional
        Name of step to use in DPPP parset
    absoluteTEC : bool, optional. Default = True
        Whether to use absolute (vTEC) or differential (dTEC) TEC.
    angRes : float, optional. Default = 60.
        Angular resolution of the screen [arcsec]. Only for turbulent model.
    ncpu : int, optional
        Number of cores to use, by default all available.
    """
    method = method.lower()
    if ncpu == 0:
        ncpu = mp.cpu_count()
    # Get sky model properties
    ras, decs = obs.get_patch_coords()
    source_names = obs.get_patch_names()
    ants = obs.stations
    sp = obs.stationpositions
    times = obs.get_times()
    
    tecvals = np.zeros((len(times), len(ants), len(ras)))
    weights = np.ones_like(tecvals)
    
    if method == 'turbulence':               
        directions = np.array([ras, decs]).T
        tecvals = comoving_tecscreen(sp, directions, times, angRes = angRes, 
                                     hIon = 200.e3, maxvtec = 50, maxdtec = 1, 
                                     ncpu = ncpu, expfolder = None, seed =seed, 
                                     absoluteTEC = absoluteTEC)           

    elif method == 'fits':
        # Load solutions from FITS cube
        hdu = pyfits.open(fitsFilename, memmap=False)
        data = hdu[0].data
        header = hdu[0].header
        w = wcs.WCS(header)
        ntimes, _, nstations, ny, nx = data.shape

        # Check that number of stations in input FITS cube matches MS
        if nstations != len(ants):
            log.error('Number of stations in input FITS cube does not '
                          'match that in the input MS')
            return 1

        # Get solutions at the source coords
        for d, (ra_deg, dec_deg) in enumerate(zip(ras, decs)):
            ra_dec = np.array([[ra_deg, dec_deg, 0, 0, 0]])
            x = int(w.wcs_world2pix(ra_dec, 0)[0][0])
            y = int(w.wcs_world2pix(ra_dec, 0)[0][1])
            if x < 0 or x > nx or y < 0 or y > ny:
                tecvals[:, :, d, :] = 0.0
                weights[:, :, d, :] = 0.0
                continue
            for t in range(ntimes):
                for s in range(nstations):
                    tecvals[t,s,d] = data[t,0,s,y,x]
        if absoluteTEC:
            tecvals = daytime_tec_modulation(times)[:,np.newaxis,np.newaxis]*(
                tecvals + maxvtec)
        else :
            tecvals = (daytime_tec_modulation(times)[:,np.newaxis,np.newaxis]
                       *tecvals) 

    elif method == 'tid':
        # Properties of TID wave
        tidLen=200e3
        tidVel=500e3/3600,
        tid_prop = [maxdtec, tidLen, tidVel]
        # Generate solutions for TID wave
        A12 = EarthLocation(lat=52.91*u.deg, lon=6.87*u.deg, height=1*u.m)
        mjd = Time(times/(3600.0 * 24.0), format="mjd")

        aa = AltAz(location=A12, obstime=mjd)
        altazcoord = []
        pool = mp.Pool(processes=ncpu)
        radec = [(r, d, aa) for r, d in zip(ras, decs)]
        altazcoord = pool.map(_getaltaz, radec)
        gettec_args = [(a, sp, A12, times, *tid_prop) for a in altazcoord]
        alltec = pool.map(_gettec, gettec_args)
        pool.close()
        pool.join()
        alltec = np.array(alltec) 
        # Fill the axis arrays
        tecvals = alltec[:, :, 0, :].transpose([2, 1, 0])#[:,:,:,0]
         # convert to vTEC
        if absoluteTEC:
            tecvals = daytime_tec_modulation(times)[:,np.newaxis,np.newaxis]*(
                tecvals + maxvtec)
        else :
            tecvals = (daytime_tec_modulation(times)[:,np.newaxis,np.newaxis]
                       *tecvals)        
            
    else:
        log.error('method "{}" not understood'.format(method))
        return 1
    
    if os.path.exists(h5parmFilename):
        log.info(h5parmFilename +' already exists. Overwriting file...')
        os.remove(h5parmFilename)
    
    # Write tec values to h5parm file as DPPP input    
    ho = h5parm(h5parmFilename, readonly=False)
    solset = ho.makeSolset(solsetName='sol000')
    st = solset.makeSoltab('tec', 'tec000', axesNames=['time','ant','dir'],
                           axesVals=[times, ants, source_names], vals=tecvals,
                           weights=weights)
    antennaTable = solset.obj._f_get_child('antenna')
    antennaTable.append(list(zip(*(ants, sp))))
    sourceTable = solset.obj._f_get_child('source')
    vals = [[ra, dec] for ra, dec in zip(ras, decs)]
    sourceTable.append(list(zip(*(source_names, vals))))

    # Add CREATE entry to history
    soltabs = solset.getSoltabs()
    for st in soltabs:
        st.addHistory('CREATE (by TEC operation of LoSiTo from obs {0} '
                      'and method="{{1}}")'.format(h5parmFilename, method))
    ho.close()

    # Update predict parset parameters for the obs
    obs.parset_parameters['predict.applycal.parmdb'] = h5parmFilename
    if 'predict.applycal.steps' in obs.parset_parameters:
        obs.parset_parameters['predict.applycal.steps'].append(stepname)
    else:
        obs.parset_parameters['predict.applycal.steps'] = [stepname]
    obs.parset_parameters['predict.applycal.correction'] = 'tec000'
    obs.parset_parameters['predict.applycal.{}.correction'.format(stepname)] = 'tec000'
    obs.parset_parameters['predict.applycal.{}.parmdb'.format(stepname)] = h5parmFilename

    return 0
