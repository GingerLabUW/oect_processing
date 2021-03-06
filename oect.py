# -*- coding: utf-8 -*-
"""
OECT.py: Contains OECT class for processing transistor data.

Created on Tue Oct 10 17:13:07 2017

__author__ = "Rajiv Giridharagopal"
__email__ = "rgiri@uw.edu"
@author: Raj
"""

import configparser
import os
import warnings

import numpy as np
import pandas as pd
from scipy import interpolate as spi
from scipy import signal as sps
from scipy.optimize import curve_fit as cf
from collections import Counter

from deriv import gm_deriv

warnings.simplefilter(action='ignore', category=FutureWarning)


class OECT:
	'''
	OECT class for processing transistor data from a folder of text files.
	The transfer curves must include 'transfer' somewhere in the filename.
	The output curves similarly should have 'output' somewhere

	This will extract the transfer curves, output curves, transconductance, and
	threshold voltage from a folder of data. 
	
	It splits all traces into a forward and reverse trace and auto-calculates
	threshold. By default it uses a smoothed derivative to get gm.
	
	The important returns are:
		outputs : DataFrame of all output curves
		transfers : DataFrame of all transfer curves
		gms : DataFrame of all transconductances
		Vts : Array of all calculated threshold voltages
		
	Usage
	--------
	>>> import OECT
	>>>
	>>> path = '../device_data/pixel_01'
	>>>
	>>> device = OECT.OECT(path)
	>>> device.calc_gms()
	>>> device.thresh()
	>>> 
	>>> from matplotlib import pyplot as plt
	>>> plt.plot(device.transfers)
	>>> plt.figure(), plt.plot(device.outputs)
	>>> plt.figure(), plt.plot(device.gms)    

	Parameters
	----------
	folder : string, optional
		path to data folder on the computer. Default prompts a file dialog
	dimDict : dict
		dictionary in format of {parentfolder1: {subfolder1: w1, l1}, {subfolder2: w2, l2}, parentfolder2...}
	params : dict, optional
		device parameters, typically Width (W), length (L), thickness (d)
	options : dict, optional
		processing optional parameters (for transfer curves only)
		Average : bool
			Whether instead to average forward and reverse trace
			Reverse XOR Average must be true
		gm_method : str
			For calculating gm from the transfer curve Id-Vg
			'sg' = Savitsky_golay smoothed derivative
			'raw' = raw derivative
			'poly' = 8th order polynomial fit

	Attributes
	----------
	Important attributes:
		
		outputs : DataFrame
			Single DataFrame of all outputs in one file.
			Assumes all data taken on same Vd range (as during an experiment)
		transfers : DataFrame
			single dataFrame with all transfer curves
		gms : DataFrame
			Transconductance for all sweeps (in Siemens) as one DataFrame
		Vts : ndarray
			Threshold voltage for forward and reverse trace
			Element 0: forward, 1: reverse
		WdL : float
			The value of W*d/L, d=thickness, W=width, L=length of the device

	Other attributes:
		
		output : dict
			dict of DataFrames
			Each DataFrame is Id-Vd, with index of DataFrame set to Vd.
			All other columns removed (Id-error, Ig, Ig-error)
		output_raw : dict
			dict of DataFrames
			same as output except columns maintained
	
		transfer : dict
			dict of DataFrames
			DataFrame of Id-Vg, with index of DataFrame set to Vg
			All other columns removed (Ig-error)
		transfer_raw : dict
			dict of DataFrames
			DataFrame of Id-Vg, with index of DataFrame set to Vg
			same as transfer except all columns maintained
	
		Vg_array : list of str
			list of gate voltages (Vg) used during Id-Vd sweeps
		Vg_labels: list of floats
			list of gate voltages (Vg) used during Id-Vd sweeps for plot labels
		transfer_avgs : int
			averages taken per point in transfer curve
	
		gm_fwd : dict
			dict of Dataframes of all forward sweep gms
		gm_bwd : dict
			dict of Dataframes of all backward sweep gms
		gm_peaks : ndarray
			Peak gms calculated by taking simple peak
		peak_gm : ndarray
			Peak gm values
		Vt : float
			Threshold voltage calculated from sqrt(Id) fit
	
		reverse : bool
			If a reverse trace exists
		rev_point : float
			Voltage where the Id trace starts reverse sweep
	'''

	def __init__(self, folder='', dimDict={}, params={}, options={}):

		# Data containers
		self.output = {}
		self.output_raw = {}
		self.outputs = pd.DataFrame()
		self.transfer = {}
		self.transfer_raw = {}
		self.transfers = pd.DataFrame()
		self.Vg_array = []
		self.Vd_array = []
		self.Vg_labels = []
		self.Vd_labels = []
		self.gm_fwd = {}
		self.gm_bwd = {}
		self.gms = pd.DataFrame()
		self.peak_gm = None

		# Data descriptors
		self.make_config = False  # if config doesn't exist, for backwards-compatibility
		self.transfer_avgs = 1
		self.folder = folder
		self.num_outputs = 0
		self.num_transfers = 0
		self.reverse = False
		self.rev_point = np.nan

		# Threshold
		self.Vt = np.nan
		self.Vts = np.nan

		if not folder:
			from PyQt5 import QtWidgets

			app = QtWidgets.QApplication([])
			self.folder = QtWidgets.QFileDialog.getExistingDirectory(caption='Select folder of data')
			print('Loading', self.folder)
			app.closeAllWindows()
			app.exit()

		# load data, finds config file
		self.filelist()
		_par, _opt = config_file(self.config)

		self.set_params(_par, _opt, params, options)
		self.loaddata()
		# 
		if dimDict: #set W and L based on dictionary
			subfolder = os.path.basename(folder)
			parentFolder = os.path.dirname(folder)
			dims = dimDict[parentFolder][subfolder]
			self.W = dims[0]
			self.params['W'] = dims[0]
			self.L = dims[1]
			self.params['L'] = dims[1]
		else:
			self.W, self.L = self.params['W'], self.params['L']

		if 'd' not in self.params:
			self.params['d'] = 40e-9
		elif self.params['d'] > 1:  # wrong units
			self.params['d'] *= 1e-9
		self.d = self.params['d']

		self.WdL = self.W * self.d / self.L

		return

	def set_params(self, par, opt, params, options):
		'''
		Sets the default parameters and reads in passed parameters/options
		
		par and opt from the config file
		params is passed from the function call
		'''
		# processing and device parameters
		self.params = {}
		self.options = {}

		# From the config flie
		for p in par:
			self.params[p] = par[p]
		for o in opt:
			self.options[o] = opt[o]

		# Overwrite with passed parameters
		if any(params):
			for p in params:
				self.params[p] = params[p]
		if any(options):
			for o in options:
				self.options[o] = options[o]

		# defaults
		if 'gm_method' not in self.options:
			self.options['gm_method'] = 'sg'
		if 'Reverse' not in self.options:
			self.options['Reverse'] = True
		if 'Average' not in self.options:
			self.options['Average'] = False
		if 'V_low' not in self.options:
			self.options['V_low'] = False
		if 'overwrite' not in self.options:
			self.options['overwrite'] = False

		return

	def loaddata(self):
		"""
		3 Steps to loading a folder of data:
			1) generate filelist for only txt files
			2) determine if config exists (newer devices)
			3) for each file in the filelist, generate a transfer curve or output curve

		"""

		for t in self.files:

			self.get_metadata(t)

			if 'transfer' in t:
				self.transfer_curve(t)

			elif 'output' in t:
				self.output_curve(t)

		self.all_outputs()

		self.all_transfers()

		self.num_transfers = len(self.transfers.columns)
		self.num_outputs = len(self.outputs.columns)

		if self.make_config:  # no proper config file found
			self.update_config()

		# can manually use options to overwrite the config file
		if 'overwrite' in self.options:
			if self.options['overwrite']:
				self.update_config()
		return

	def filelist(self):
		""" Generates list of files to process and config file"""

		filelist = os.listdir(self.folder)
		files = [os.path.join(self.folder, name)
				 for name in filelist if name[-3:] == 'txt']

		# find config file
		config = [os.path.join(self.folder, name)
				  for name in filelist if name[-4:] == '.cfg']

		if config:

			for f in files:

				if 'config' in f:
					files.remove(f)

			self.config = config

		else:

			print('No config file found!')
			path = '\\'.join(files[0].split('\\')[:-1])
			self.config = make_config(path)
			self.make_config = True

		self.files = files

		return

	def get_metadata(self, fl):
		""" Called in load_data to extract file-specific parameters """

		# search params in first file in this folder for missing params
		h = open(fl)
		for line in h:
			if 'V_DS = ' in line:
				self.Vd = float(line.split()[-1])
			if 'V_G = ' in line:
				self.Vg = float(line.split()[-1])

			# if no config file found, populate based on the raw data
			if 'Width/um' in line and (self.make_config or self.options['overwrite']):
				self.W = float(line.split()[-1])
			if 'Length/um' in line and (self.make_config or self.options['overwrite']):
				self.L = float(line.split()[-1])

		h.close()

		return

	def _reverse(self, v, transfer=False):
		"""if reverse trace exists, return inflection-point index and flag
		
		transfer : bool, optional
			We only want to save rev_point and rev_v for the transfer curve
			
		Returns:
			mx : index where the voltage reverses
		"""

		# find inflection point where trace reverses

		# First, check if any voltages repeated (indicates both a fwd/rev sweep)
		# two ways to do this

		# a) find reverse sweep using np.allclose
		midpoint = len(v)//2 + 1
		if len(v) % 2 == 1:
			x = v[:len(v)//2]
			y = np.flip(v[midpoint:])
		else:
			x = v[1:len(v)//2]
			y = np.flip(v[midpoint:])
		reverse = np.allclose(x, y)

		# # b) find reverse sweep using Counter		
		# counts = Counter(v).most_common()
		# reverse = counts[0][1] > 1

		# Then, find where the voltage range reverses (gradient = 0)
		if reverse:

			mx = midpoint - 1

			if transfer:
				self.rev_point = mx  # find inflection
				self.rev_v = v[mx]
				self.options['Reverse'] = True
				self.reverse = True

			return mx, True

		else:

			mx = len(v) - 1

			if transfer:
				self.rev_point = mx
				self.rev_v = v[mx]
				self.options['Reverse'] = False
				self.reverse = False

			return mx, False

	def calc_gms(self):
		"""
		Calculates all the gms in the set of data.
		Assigns each one to gm_fwd (forward) and gm_bwd (reverse) as a dict

		Creates a single dataFrame gms_fwd and another gms_bwd
		"""

		for i in self.transfer:
			self.gm_fwd[i], self.gm_bwd[i], self.gm_peaks = self._calc_gm(self.transfer[i])

		# combine all the gm_fwd and gm_bwd into a single dataframe
		labels = 0

		for g in self.gm_fwd:

			if not self.gm_fwd[g].empty:

				gm = self.gm_fwd[g].values.flatten()
				idx = self.gm_fwd[g].index.values

				nm = 'gm_' + g

				while nm in self.gms:
					labels += 1
					nm = 'gm_' + g[:-1] + str(labels)

				df = pd.Series(data=gm, index=idx)
				df.sort_index(inplace=True)
				self.gms[nm] = df

		for g in self.gm_bwd:

			if not self.gm_bwd[g].empty:

				gm = self.gm_bwd[g].values.flatten()
				idx = self.gm_bwd[g].index.values

				nm = 'gm_' + g

				while nm in self.gms:
					labels += 1
					nm = 'gm_' + g[:-1] + str(labels)

				df = pd.Series(data=gm, index=idx)
				df.sort_index(inplace=True)
				self.gms[nm] = df

		self.peak_gm = self.gm_peaks['peak gm (S)'].values
		return

	def _calc_gm(self, df):
		"""
		Calculates single gm curve in milli-Siemens
		Splits data into "forward" and "backward"
		Assumes curves taken neg to positive Vg

		df = dataframe
		"""

		v = np.array(df.index)
		i = np.array(df.values)

		mx, reverse = self.rev_point, self.reverse

		vl_lo = np.arange(v[0], v[mx], 0.01)
		vl_lo = v[:mx]

		gm_peaks = np.array([])
		gm_args = np.array([])

		# sg parameters
		window = np.max([int(0.04 * self.transfers.shape[0]), 3])
		polyorder = 2
		deg = 8
		fitparams = {'window': window, 'polyorder': polyorder, 'deg': deg}

		def get_gm(v, i, fit, options):

			gml = gm_deriv(v, i, fit, options)
			gm = pd.DataFrame(data=gml, index=v, columns=['gm'])
			gm.index.name = 'Voltage (V)'

			return gm

		# Get gm
		gm_fwd = get_gm(vl_lo, i[0:mx], self.options['gm_method'], fitparams)
		gm_peaks = np.append(gm_peaks, np.max(gm_fwd.values))
		gm_args = np.append(gm_args, gm_fwd.index[np.argmax(gm_fwd.values)])

		# if reverse trace exists and we want to process it
		if reverse:
			vl_hi = np.flip(v[mx:])
			i_hi = np.flip(i[mx:])

			gm_bwd = get_gm(vl_hi, i_hi, self.options['gm_method'], fitparams)

			gm_peaks = np.append(gm_peaks, np.max(gm_bwd.values))
			gm_args = np.append(gm_args, gm_bwd.index[np.argmax(gm_bwd.values)])

		else:

			gm_bwd = pd.DataFrame()  # empty dataframe

		gm_peaks = pd.DataFrame(data=gm_peaks, index=gm_args, columns=['peak gm (S)'])
		print(gm_peaks)
		return gm_fwd, gm_bwd, gm_peaks

	def output_curve(self, path):
		"""Loads Id-Vd output curves from a folder as Series in a list"""

		V = self.Vg

		op = pd.read_csv(path, delimiter='\t', engine='python')

		# Remove junk rows
		_junk = pd.to_numeric(op['V_DS'], errors='coerce')
		_junk = _junk.notnull()
		op = op.loc[_junk]
		op = op.set_index('V_DS')
		op = op.set_index(pd.to_numeric(op.index.values))

		mx, reverse = self._reverse(op.index.values, transfer=False)
		idx = op.index.values[mx]

		self.Vg_array.append(V)
		Vfwd = str(V) + '_fwd'
		self.output[Vfwd] = op[:idx]
		self.output_raw[Vfwd] = op[:idx]
		self.output[Vfwd] = self.output[Vfwd].drop(['I_DS Error (A)',
													'I_G (A)',
													'I_G Error (A)'], 1)
		if reverse:
			Vbwd = str(V) + '_bwd'
			self.output[Vbwd] = op[idx:]
			self.output_raw[Vbwd] = op[idx:]
			self.output[Vbwd] = self.output[Vbwd].drop(['I_DS Error (A)',
														'I_G (A)',
														'I_G Error (A)'], 1)

	def all_outputs(self):
		"""
		Creates a single dataFrame with all output curves
		This assumes that all data were taken at the same Vds range
		"""
		self.Vg_labels = []  # corrects for labels below

		for op in self.output:
			self.Vg_labels.append(op)
			df = pd.DataFrame(self.output[op])
			df = df.rename(columns={self.output[op].columns[0]: op})

			if self.outputs.empty:
				self.outputs = pd.DataFrame(df)
			else:
				self.outputs = pd.concat([self.outputs, df], axis=1)

		self.num_outputs = len(self.outputs.columns)
		return

	def transfer_curve(self, path):
		"""Loads Id-Vg transfer curve from a path"""
		transfer_raw = pd.read_csv(path, delimiter='\t', engine='python')

		# Remove junk rows
		_junk = pd.to_numeric(transfer_raw['V_G'], errors='coerce')
		_junk = _junk.notnull()
		transfer_raw = transfer_raw.loc[_junk]
		transfer_raw = transfer_raw.set_index('V_G')
		transfer_raw = transfer_raw.set_index(pd.to_numeric(transfer_raw.index.values))

		transfer_Vd = str(self.Vd)

		if (transfer_Vd + '_0') in self.transfer:
			c = list(self.transfer.keys())[-1]
			c = str(int(c[-1]) + 1)
			transfer_Vd = transfer_Vd + '_' + c

		else:
			transfer_Vd += '_0'

		self.transfer[transfer_Vd] = transfer_raw
		self.transfer_raw[transfer_Vd] = transfer_raw
		self.transfer[transfer_Vd] = self.transfer[transfer_Vd].drop(['I_DS Error (A)', 'I_G (A)',
																	  'I_G Error (A)'], 1)

		return

	def all_transfers(self):

		"""
		Creates a single dataFrame with all transfer curves (in case more than 1)
		This assumes that all data were taken at the same Vgs range
		"""
		for tf in self.transfer:
			self.Vd_labels.append(tf)

			transfer = self.transfer[tf]['I_DS (A)'].values
			idx = self.transfer[tf]['I_DS (A)'].index.values

			mx, reverse = self._reverse(idx, transfer=True)
			nm = tf + '_01'
			df = pd.Series(data=transfer[:mx], index=idx[:mx])
			df.sort_index(inplace=True)
			self.transfers[nm] = df

			if reverse:
				nm = tf + '_02'
				df = pd.Series(data=transfer[mx:], index=idx[mx:])
				df.sort_index(inplace=True)
				self.transfers[nm] = df

		if 'Average' in self.options and self.options['Average']:
			self.transfers = self.transfers.mean(1)

		# if there's an "inversion" at the end, finds that point
		if self.options['V_low'] is True:

			for e in self.transfers:

				df = self.transfers[e].copy()
				df.sort_index(inplace=True)

				vdx = df.index.values
				idx = df.values

				for x in np.arange(len(vdx) - 1):

					if (idx[x + 1] - idx[x]) > 0:
						break

				cut = vdx[x]

				self.transfers = self.transfers[cut:]

		return

	def quadrant(self):

		if np.any(self.gm_peaks.index < 0):

			self.quad = 'III'  # positive voltage, positive current

		elif np.any(self.gm_peaks.index > 0):

			self.quad = 'I'  # negative voltage, negative curret=nt

		return

	def thresh(self, plot=False):
		"""
		Finds the threshold voltage by fitting sqrt(Id) vs (Vg-Vt) and finding
			x-offset

		plot : bool, Optional
			To show the threshold fit and line

		"""

		Vts = np.array([])
		VgVts = np.array([])

		v_lo = self.transfers.index.values

		if plot:
			from matplotlib import pyplot as plt
			plt.figure()
			plt.xlabel('$V_{GS}$ $Voltage (V)$')
			plt.ylabel('|$I_{DS}$$^{0.5}$| ($A^{0.5}$)')
			labels = []

		# Find and fit at inflection between regimes
		for tf, pk in zip(self.transfers, self.gm_peaks.index):
			# use second derivative to find inflection, then fit line to get Vt
			Id_lo = np.sqrt(np.abs(self.transfers[tf]).values)

			# minimize residuals by finding right peak
			fit = self._min_fit(Id_lo - np.min(Id_lo), v_lo)

			if plot:
				plt.plot(np.sqrt(np.abs(self.transfers[tf])), 'bo-')
				v = self.transfers[tf].index.values
				tx = np.arange(np.min(v), -fit[1] / fit[0] + 0.1, 0.01)

				if self.quad == 'I':
					tx = np.arange(-fit[1] / fit[0] - 0.1, np.max(v), 0.01)

				plt.plot(tx, self.line_f(tx, *fit), 'r--')
				labels.append('{:.4f}'.format(-fit[1] / fit[0]))

			# fits line, finds threshold from x-intercept
			Vts = np.append(Vts, -fit[1] / fit[0])  # x-intercept
			VgVts = np.append(VgVts, np.abs(pk + fit[1] / fit[0]))  # Vg - Vt, + sign from -fit[1]/fit[0]

		if plot:
			plt.legend(labels=labels)
			plt.axhline(0, color='k', linestyle='--')
			for v in Vts:
				plt.axvline(v, color='k', linestyle='--')

		self.Vt = np.mean(Vts)
		self.Vts = Vts
		self.VgVt = np.mean(VgVts)
		self.VgVts = VgVts

		return

	# find minimum residual through fitting a line to several found peaks
	def _min_fit(self, Id, V):

		_residuals = np.array([])
		_fits = np.array([0, 0])

		# splines needs to be ascending
		if V[2] < V[1]:
			V = np.flip(V)
			Id = np.flip(Id)

		self.quadrant()

		if self.quad == 'I':  # top right

			Id = np.flip(Id)
			V = np.flip(-V)

		mx_d2 = self._find_peak(Id * 1000, V)  # *1000 improves numerical spline accuracy

		# sometimes for very small currents run into numerical issues
		if not mx_d2:
			mx_d2 = self._find_peak(Id * 1000, V, width=15)

		# for each peak found, fits a line. Uses that to determine Vt, then residual up to that found Vt
		for m in mx_d2:
			# Id = Id - np.min(Id) # 0-offset

			fit, _ = cf(self.line_f, V[:m], Id[:m],
						bounds=([-np.inf, -np.inf], [0, np.inf]))

			v_x = np.searchsorted(V, -fit[1] / fit[0])  # finds the Vt from this fit to determine residual
			_res = np.sum(np.array((Id[:v_x] - self.line_f(V[:v_x], fit[0], fit[1])) ** 2))
			_fits = np.vstack((_fits, fit))
			_residuals = np.append(_residuals, _res)

		_fits = _fits[1:, :]
		fit = _fits[np.argmin(_residuals), :]

		if self.quad == 'I':
			fit[0] *= -1

		return fit

	# linear curve-fitting
	@staticmethod
	def line_f(x, f0, f1):

		return f1 + f0 * x

	@staticmethod
	def _find_peak(I, V, negative_Vt=True, width=15):
		"""
		Uses spline to find the transition point then return it for fitting Vt
		  to sqrt(Id) vs Vg

		Parameters
		----------
		I : array
			Id vs Vg, currents
		V : array
			Id vs Vg, voltages
		negative_Vt : bool
			Assumes Vt is a negative voltage (typical for many p-type polymer)
		width : int
			Width to use in CWT peak-finder. 


		Returns
		-------
		mxd2 : list
			index of the maximum transition point for threshold voltage calculation
		"""

		# uses second derivative for transition point
		Id_spl = spi.UnivariateSpline(V, I, k=5, s=1e-7)
		V_spl = np.arange(V[0], V[-1], 0.005)
		d2 = np.gradient(np.gradient(Id_spl(V_spl)))

		peaks = sps.find_peaks_cwt(d2, np.arange(1, width))
		peaks = peaks[peaks > 5]  # edge errors

		# find splined index in original array
		mx_d2 = [np.searchsorted(V, V_spl[p]) for p in peaks]

		return mx_d2

	def update_config(self):

		config = configparser.ConfigParser()
		config.read(self.config)

		# Update with parameters read in earlier in loaddata()
		config['Dimensions']['Width (um)'] = str(self.W)
		config['Dimensions']['Length (um)'] = str(self.L)
		config['Transfer']['Vds (V)'] = str(self.Vd)

		config['Output'] = {'Preread (ms)': 500.0,
							'First Bias (ms)': 200.0}
		config['Output']['Output Vgs'] = str(len(self.Vg_array))
		for v in range(len(self.Vg_array)):
			config['Output']['Vgs (V) ' + str(v)] = str(self.Vg_array[v])

		# overwrite the file
		try:
			with open(self.config, 'w') as configfile:

				config.write(configfile)
		except:

			with open(self.config[0], 'w') as configfile:

				config.write(configfile)

		return


def config_file(cfg):
	"""
	Generates parameters from supplied config file
	"""
	config = configparser.ConfigParser()
	config.read(cfg)
	params = {}
	options = {}

	dim_keys = {'Width (um)': 'W', 'Length (um)': 'L', 'Thickness (nm)': 'd'}
	vgs_keys = ['Preread (ms)', 'First Bias (ms)', 'Vds (V)']
	vds_keys = ['Preread (ms)', 'First Bias (ms)', 'Output Vgs']
	opts_bools = ['Reverse', 'Average']
	opts_str = ['gm_method']
	opts_flt = ['V_low']

	for key in dim_keys:

		if config.has_option('Dimensions', key):
			params[dim_keys[key]] = config.getfloat('Dimensions', key)

	for key in vgs_keys:

		if config.has_option('Transfer', key):
			params[key] = int(config.getfloat('Transfer', key))

	for key in vds_keys:

		if config.has_option('Output', key):
			val = int(config.getfloat('Output', key))

			# to avoid duplicate keys
			if key in params:
				key = 'output_' + key
			params[key] = val

	if 'Output Vgs' in params:

		params['Vgs'] = []
		for i in range(0, params['Output Vgs']):
			nm = 'Vgs (V) ' + str(i)

			val = config.getfloat('Output', nm)
			params['Vgs'].append(val)

	if 'Options' in config.sections():

		for key in opts_bools:

			if config.has_option('Options', key):
				options[key] = config.getboolean('Options', key)

		for key in opts_str:

			if config.has_option('Options', key):
				options[key] = config.get('Options', key)

		for key in opts_flt:

			if config.has_option('Options', key):
				options[key] = config.getfloat('Options', key)

	return params, options


def make_config(path):
	'''
	If a config file does not exist, this will generate one automatically.
	
	'''
	config = configparser.ConfigParser()
	config.optionxform = str

	config['Dimensions'] = {'Width (um)': 2000, 'Length (um)': 20}
	config['Transfer'] = {'Preread (ms)': 30000.0,
						  'First Bias (ms)': 120000.0,
						  'Vds (V)': -0.60}

	config['Output'] = {'Preread (ms)': 500.0,
						'First Bias (ms)': 200.0,
						'Output Vgs': 4,
						'Vgs (V) 0': -0.1,
						'Vgs (V) 1': -0.3,
						'Vgs (V) 2': -0.5,
						'Vgs (V) 3': -0.9}

	with open(path + r'\config.cfg', 'w') as configfile:
		config.write(configfile)

	return path + r'\config.cfg'
