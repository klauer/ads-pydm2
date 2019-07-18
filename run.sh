#!/bin/bash

export DYLD_LIBRARY_PATH=/Users/klauer/docs/Repos/pyads/adslib

export PYQTDESIGNERPATH=/Users/klauer/Repos/pydm2/
export PYDM_DATA_PLUGINS_PATH=$PWD
alias designer='/Users/klauer/mc/envs/pydm2/bin/Designer.app/Contents/MacOS/Designer'

pydm -m host=172.21.148.145 -m port=851 test.ui
