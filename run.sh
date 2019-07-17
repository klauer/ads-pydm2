#!/bin/bash

export DYLD_LIBRARY_PATH=/Users/klauer/docs/Repos/pyads/adslib

export PYQTDESIGNERPATH=/Users/klauer/Repos/pydm2/
export PYDM_DATA_PLUGINS_PATH=$PWD
alias designer='/Users/klauer/mc/envs/pydm2/bin/Designer.app/Contents/MacOS/Designer'

pydm test.ui -m P=ads://172.21.42.145.1.1/
