#!/usr/bin/env python
# -*- coding: utf-8 -*-
import logging as log
import numpy as np
from losoto.h5parm import h5parm

"""
Polarization misalignment operation for losito: simulate a constant station-
and polarization-dependent delay."""
log.debug('Loading POLMISALIGN module.')


def _run_parser(obs, parser, step):
    h5parmFilename = parser.getstr(step, 'h5parmFilename', 'corruptions.h5')
    seed = parser.getint(step, 'seed', default=0)
    polDelay = parser.getfloat(step, 'polDelay', default=1.0e-9)

    parser.checkSpelling(step, ['h5parmFilename', 'seed', 'polDelay'])
    return run(obs, h5parmFilename, seed, polDelay, step)


def run(obs, h5parmFilename, seed=0, polDelay=1e-9, stepname='pol_misalign'):
    '''
    Simulate polarization misalignment.
    
    Parameters
    ----------
    seed : unsigned int, optional. default = 0
        Set the random seed. Seed = 0 (default) will set no seed.
    polDelay : float, optional. The default value is 1e-9 s.
        Standard deviation of the polarization dependent constant clok delay.
    '''
    if seed != 0:  # Set random seed if provided.
        np.random.seed(int(seed))

    stations = obs.stations
    ras, decs = obs.get_patch_coords()
    source_names = obs.get_patch_names()
    pol = np.array(['XX', 'YY'])

    # draw time delays and reference them w.r.t. station 1.
    # Polarization Y is delayed w.r.t. X
    delays = np.zeros((2, len(stations)))
    delays[1] = np.random.normal(0, polDelay, len(stations))
    delays[1] -= delays[1, 0]
    weights = np.ones_like(delays)

    # Write polarization misalignment values to h5parm file as DP3 input.
    ho = h5parm(h5parmFilename, readonly=False)
    if 'sol000' in ho.getSolsetNames():
        solset = ho.getSolset('sol000')
    else:
        solset = ho.makeSolset(solsetName='sol000')

    # Definition: clock001 is pol misalignment, clock000 is clock delay.
    if 'clock001' in solset.getSoltabNames():
        log.info('''Solution-table clock001 is already present in
                 {}. It will be overwritten.'''.format(h5parmFilename + '/clock001'))
        solset.getSoltab('clock001').delete()

    st = solset.makeSoltab('clock', 'clock001', axesNames=['pol', 'ant'],
                           axesVals=[pol, stations],
                           vals=delays, weights=weights)

    antennaTable = solset.obj._f_get_child('antenna')
    antennaTable.append(list(zip(*(stations, obs.stationpositions))))
    sourceTable = solset.obj._f_get_child('source')
    ras, decs = obs.get_patch_coords()
    source_names = obs.get_patch_names()
    vals = [[ra, dec] for ra, dec in zip(ras, decs)]
    sourceTable.append(list(zip(*(source_names, vals))))

    soltabs = solset.getSoltabs()
    for st in soltabs:
        st.addHistory('CREATE (by POLMISALIGN operation of LoSiTo from obs {0})'.format(h5parmFilename))
    ho.close()

    # Update predict parset parameters for the obs
    obs.add_to_parset(stepname, 'clock001', h5parmFilename, DDE=False)

    return 0
