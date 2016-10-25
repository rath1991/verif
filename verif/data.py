from scipy import io
import numpy as np
import re
import sys
import os
import verif.input
from matplotlib.dates import *
import matplotlib.ticker
import verif.field
import verif.util

class Data(object):
   """ Organizes data from several inputs

   Access verification data from a list of verif.input. Only returns data that
   is available for all files, for fair comparisons i.e if some
   dates/offsets/locations are missing.

   Instance attribute:
   dates          A numpy array of available dates
   offsets        A numpy array of available leadtimes
   locations      A list of available locations
   thresholds     A numpy array of available thresholds
   quantiles      A numpy array of available quantiles
   num_inputs     The number of inputs in the dataset
   variable       The variable
   months      
   years       
   """
   def __init__(self, inputs, dates=None, offsets=None, locations=None,
         lat_range=None, lon_range=None, elev_range=None, clim=None, clim_type="subtract",
         legend=None, remove_missing_across_all=True):

      """
      Arguments:
      inputs         A list of verif.input
      dates          A numpy array of dates. Discard data for all other dates
      offsets        A numpy array of offsets. Discard data for all other offsets
      locations      A list of verif.location. Discard data for all other locations
      clim           Use this NetCDF file to compute anomaly. Should therefore
                     be a climatological forecast. Subtract/divide the
                     forecasts from this file from all forecasts and
                     observations from the other files.
      clim_type      Operation to apply with climatology. Either 'subtract', or
                     'divide'
      """
   
      if(not isinstance(inputs, list)):
         inputs = [inputs]
      self._remove_missing_across_all = remove_missing_across_all

      if(legend is not None and len(inputs) is not len(legend)):
         verif.util.error("Need one legend entry for each filename")
      self._legend = legend

      # Organize inputs
      self._inputs = list()
      self._cache = list()
      self._clim = None
      for input in inputs:
         self._inputs.append(input)
         self._cache.append(dict())
      if(clim is not None):
         self._clim = verif.input.get_input(clim)
         self._cache.append(dict())
         if(not (clim_type == "subtract" or clim_type == "divide")):
            verif.util.error("Data: clim_type must be 'subtract' or 'divide")
         self._clim_type = clim_type

         # Add climatology to the end
         self._inputs = self._inputs + [self._clim]

      # Latitude-Longitude range
      if(lat_range is not None or lon_range is not None):
         lat = [loc.lat for loc in self._inputs[0].locations]
         lon = [loc.lon for loc in self._inputs[0].locations]
         loc_id = [loc.id for loc in self._inputs[0].locations]
         latlon_locations = list()
         min_lon = -180
         max_lon = 180
         min_lat = -90
         max_lat = 90
         if lat_range is not None:
            min_lat = lat_range[0]
            max_lat = lat_range[1]
         if lon_range is not None:
            min_lon = lon_range[0]
            max_lon = lon_range[1]
         for i in range(0, len(lat)):
            currLat = float(lat[i])
            currLon = float(lon[i])
            if(currLat >= min_lat and currLat <= max_lat and
                  currLon >= min_lon and currLon <= max_lon):
               latlon_locations.append(loc_id[i])
         use_locationss = list()
         if(locations is not None):
            for i in range(0, len(locations)):
               currLocation = locations[i]
               if(currLocation in latlon_locations):
                  use_locationss.append(currLocation)
         else:
            use_locationss = latlon_locations
         if(len(use_locationss) == 0):
            verif.util.error("No available locations within lat/lon range")
      elif locations is not None:
         use_locationss = locations
      else:
         use_locationss = [s.id for s in self._inputs[0].locations]

      # Elevation range
      if(elev_range is not None):
         locations = self._inputs[0].locations
         min_elev = elev_range[0]
         max_elev = elev_range[1]
         elev_locations = list()
         for i in range(0, len(locations)):
            curr_elev = float(locations[i].elev())
            id = locations[i].id()
            if(curr_elev >= min_elev and curr_elev <= max_elev):
               elev_locations.append(id)
         use_locationss = verif.util.intersect(use_locationss, elev_locations)
         if(len(use_locationss) == 0):
            verif.util.error("No available locations within elevation range")

      # Find common indicies
      self._datesI = self._get_common_indices(self._inputs, "Date", dates)
      self._offsetsI = self._get_common_indices(self._inputs, "Offset", offsets)
      self._locationsI = self._get_common_indices(self._inputs, "Location", use_locationss)
      if(len(self._datesI[0]) == 0):
         verif.util.error("No valid dates selected")
      if(len(self._offsetsI[0]) == 0):
         verif.util.error("No valid offsets selected")
      if(len(self._locationsI[0]) == 0):
         verif.util.error("No valid locations selected")

      # Load dimension information
      self.dates = self._get_dates()
      self.offsets = self._get_offsets()
      self.locations = self._get_locations()
      self.thresholds = self._get_thresholds()
      self.quantiles = self._get_quantiles()
      self.variable = self._get_variable()
      self.months = self._get_months()
      self.years = self._get_years()
      self.num_inputs = self._get_num_inputs()

   def get_scores(self, fields, input_index, axis=verif.axis.All, axis_index=None):
      """ Retrieves scores from all files

      Climatology is handled by subtracting clim's deterministic field from any
      obs or determinsitic fields.
      
      Arguments:
      fields         A list of verif.field to retrieve
      input_index    Which input to pull from? Must be between 0 and num_inputs
      axis           Which axis to aggregate against. If verif.axis.All is
                     used, then no aggregation takes place and the 3D numpy
                     array is returned.
      axis_index     Which slice along the axis to retrieve

      Returns:
      scores         A list of numpy arrays
      """

      if input_index < 0 or input_index >= self.num_inputs:
         verif.util.error("input_index must be between 0 and %d" % self.num_inputs)

      scores = list()
      valid = None

      if(not isinstance(fields, list)):
         fields = [fields]

      # Compute climatology, if needed
      obsFcstAvailable = (verif.field.Obs in fields or verif.field.Deterministic in fields)
      doClim = self._clim is not None and obsFcstAvailable
      if(doClim):
         temp = self._get_score(verif.field.Deterministic, len(self._inputs) - 1)
         if(axis == verif.axis.Date):
            clim = temp[axis_index, :, :].flatten()
         elif(axis == verif.axis.Month):
            if(axis_index == self.months.shape[0]-1):
               I = np.where(self.dates >= self.months[axis_index])
            else:
               I = np.where((self.dates >= self.months[axis_index]) &
                            (self.dates < self.months[axis_index + 1]))
            clim = temp[I, :, :].flatten()
         elif(axis == verif.axis.Year):
            if(axis_index == self.years.shape[0]-1):
               I = np.where(self.dates >= self.years[axis_index])
            else:
               I = np.where((self.dates >= self.years[axis_index]) &
                            (self.dates < self.years[axis_index + 1]))
            clim = temp[I, :, :].flatten()
         elif(axis == verif.axis.Offset):
            clim = temp[:, axis_index, :].flatten()
         elif(verif.axis.is_location_like(axis)):
            clim = temp[:, :, axis_index].flatten()
         elif(axis == verif.axis.No or axis == verif.axis.Threshold):
            clim = temp.flatten()
         elif(axis == verif.axis.All or axis == None):
            clim = temp
      else:
         clim = 0
      
      # Load scores and flatten along the correct dimension
      for i in range(0, len(fields)):
         field = fields[i]
         temp = self._get_score(field, input_index)

         if(axis == verif.axis.Date):
            curr = temp[axis_index, :, :].flatten()
         elif(axis == verif.axis.Month):
            if(axis_index == self.months.shape[0] - 1):
               I = np.where(self.dates >= self.months[axis_index])
            else:
               I = np.where((self.dates >= self.months[axis_index]) &
                            (self.dates < self.months[axis_index + 1]))
            curr = temp[I, :, :].flatten()
         elif(axis == verif.axis.Year):
            if(axis_index == self.years.shape[0] - 1):
               I = np.where(self.dates >= self.years[axis_index])
            else:
               I = np.where((self.dates >= self.years[axis_index]) &
                            (self.dates < self.years[axis_index + 1]))
            curr = temp[I, :, :].flatten()
         elif(axis == verif.axis.Offset):
            curr = temp[:, axis_index, :].flatten()
         elif(verif.axis.is_location_like(axis)):
            curr = temp[:, :, axis_index].flatten()
         elif(axis == verif.axis.No or axis == verif.axis.Threshold):
            curr = temp.flatten()
         elif(axis == verif.axis.All or axis is None):
            curr = temp
         else:
            verif.util.error("Data.py: unrecognized axis: " + axis)

         # Subtract climatology
         if(doClim and (field == verif.field.Deterministic or field == verif.field.Obs)):
            if(self._clim_type == "subtract"):
               curr = curr - clim
            else:
               curr = curr / clim

         # Remove missing values
         if axis is not verif.axis.All:
            currValid = (np.isnan(curr) == 0)\
                      & (np.isinf(curr) == 0)
            if(valid is None):
               valid = currValid
            else:
               valid = (valid & currValid)

         scores.append(curr)
      if axis is not verif.axis.All:
         I = np.where(valid)
         for i in range(0, len(fields)):
            scores[i] = scores[i][I]

      # No valid data. Therefore return a list of nans instead of an empty list
      if(scores[0].shape[0] == 0):
         scores = [np.nan * np.zeros(1, float) for i in range(0, len(fields))]

      return scores

   def get_axis_size(self, axis):
      return len(self.get_axis_values(axis))

   # What values represent this axis?
   def get_axis_values(self, axis):
      if(axis == verif.axis.Date):
         # TODO: Does it make sense to convert here, but not with data.dates?
         return verif.util.convert_dates(self.dates)
      elif(axis == verif.axis.Month):
         return verif.util.convert_dates(self.months)
      elif(axis ==verif.axis.Year):
         return verif.util.convert_dates(self.years)
      elif(axis ==verif.axis.Offset):
         return self.offsets
      elif(axis == verif.axis.No):
         return [0]
      elif(verif.axis.is_location_like(axis)):
         if(axis == verif.axis.Location):
            data = range(0, len(self.locations))
         elif(axis == verif.axis.LocationId):
            data = self.get_location_ids()
         elif(axis == verif.axis.Elev):
            data = self.get_elevs()
         elif(axis == verif.axis.Lat):
            data = self.get_lats()
         elif(axis == verif.axis.Lon):
            data = self.get_lons()
         else:
            verif.util.error("Data.get_axis_values has a bad axis name: " + axis)
         return data
      else:
         return [0]

   def get_axis_locator(self, axis):
      """ Where should ticks be located for this axis? Returns an mpl Locator """
      if(axis == verif.axis.Offset):
         # Define our own locators, since in general we want multiples of 24
         # (or even fractions thereof) to make the ticks repeat each day. Aim
         # for a maximum of 12 ticks.
         offsets = self.get_axis_values(verif.axis.Offset)
         span = max(offsets) - min(offsets)
         if(span > 300):
            return matplotlib.ticker.AutoLocator()
         elif(span > 200):
            return matplotlib.ticker.MultipleLocator(48)
         elif(span > 144):
            return matplotlib.ticker.MultipleLocator(24)
         elif(span > 72):
            return matplotlib.ticker.MultipleLocator(12)
         elif(span > 36):
            return matplotlib.ticker.MultipleLocator(6)
         elif(span > 12):
            return matplotlib.ticker.MultipleLocator(3)
         else:
            return matplotlib.ticker.MultipleLocator(1)
      else:
         return matplotlib.ticker.AutoLocator()

   def get_full_names(self):
      names = [input.fullname for input in self._inputs]
      return names

   def get_names(self):
      names = [input.name for input in self._inputs]
      return names

   def get_short_names(self):
      return [input.shortname for input in inputs]

   def get_legend(self):
      if(self._legend is None):
         legend = self.get_names()
      else:
         legend = self._legend
      return legend

   def get_variable_and_units(self):
      var = self.variable
      return var.name + " (" + var.units + ")"

   def get_axis_label(self, axis):
      if(axis == verif.axis.Date):
         return "Date"
      elif(axis == verif.axis.Offset):
         return "Lead time (h)"
      elif(axis == verif.axis.Month):
         return "Month"
      elif(axis == verif.axis.Year):
         return "Year"
      elif(axis == verif.axis.Elev):
         return "Elevation (m)"
      elif(axis == verif.axis.Lat):
         return "Latitude ($^o$)"
      elif(axis == verif.axis.Lon):
         return "Longitude ($^o$)"
      elif(axis == verif.axis.Threshold):
         return self.get_variable_and_units()

   def get_lats(self):
      return np.array([loc.lat for loc in self.locations])

   def get_lons(self):
      return np.array([loc.lon for loc in self.locations])

   def get_elevs(self):
      return np.array([loc.elev for loc in self.locations])

   def get_location_ids(self):
      return np.array([loc.id for loc in self.locations], int)

   def get_axis_descriptions(self, axis, csv=False):
      if verif.axis.is_location_like(axis):
         descs = list()
         ids = self._get_score("Location")
         lats = self._get_score("Lat")
         lons = self._get_score("Lon")
         elevs = self._get_score("Elev")
         if csv:
            fmt = "%d,%f,%f,%f"
         else:
            fmt = "%6d %5.2f %5.2f %5.0f"
         for i in range(0, len(ids)):
            string = fmt % (ids[i], lats[i], lons[i], elevs[i])
            descs.append(string)
         return descs
      if(verif.axis.is_date_like(axis)):
         values = self.get_axis_values(axis)
         values = num2date(values)
         dates = list()
         for i in range(0, len(values)):
            dates = dates + [values[i].strftime("%Y/%m/%d")]
         return dates
      else:
         return self.get_axis_values(axis)

   def get_axis_description_header(self, axis, csv=False):
      if verif.axis.is_location_like(axis):
         if csv:
            fmt = "%s,%s,%s,%s"
         else:
            fmt = "%6s %5s %5s %5s"
         return fmt % ("id", "lat", "lon", "elev")
      else:
         return verif.axis.get_name(axis)

   def _get_score(self, field, input_index):
      """ Load the field variable from input, but only include the common data
      
      Scores loaded will have the same dimension, regardless what input_index
      is used.

      field:         The type is of verif.field
      input_index:   which input to load from
      """

      # Check if data is cached
      if(field in self._cache[input_index]):
         return self._cache[input_index][field]

      # Load all inputs
      for i in range(0, self._get_num_inputs_with_clim()):
         if(field not in self._cache[i]):
            input = self._inputs[i]
            assert(verif.field.Threshold(1) == verif.field.Threshold(1))
            if(field not in input.get_variables()):
               verif.util.error("%s does not contain '%s'" %
                     (self.get_names()[i], field.name()))
            if field is verif.field.Obs:
               temp = input.obs
            elif field is verif.field.Deterministic:
               temp = input.deterministic
            elif field is verif.field.Ensemble:
               temp = input.ensemble[:,:,:,field.member]
            elif field.__class__ is verif.field.Threshold:
               I = np.where(input.thresholds == field.threshold)[0]
               assert(len(I) == 1)
               temp = input.threshold_scores[:,:,:,I]
            elif field.__class__ is verif.field.Quantile:
               I = np.where(input.quantiles == field.quantile)[0]
               assert(len(I) == 1)
               temp = input.quantile_scores[:,:,:,I]
            else:
               verif.util.error("Not implemented")
            Idates = self._get_date_indices(i)
            Ioffsets = self._get_offset_indices(i)
            Ilocations = self._get_location_indices(i)
            temp = temp[Idates, :, :]
            temp = temp[:, Ioffsets, :]
            temp = temp[:, :, Ilocations]
            self._cache[i][field] = temp

      # Remove missing. If one configuration has a missing value, set all
      # configurations to missing This can happen when the dates are available,
      # but have missing values
      if self._remove_missing_across_all:
         is_missing = np.isnan(self._cache[0][field])
         for i in range(1, self._get_num_inputs_with_clim()):
            is_missing = is_missing | (np.isnan(self._cache[i][field]))
         for i in range(0, self._get_num_inputs_with_clim()):
            self._cache[i][field][is_missing] = np.nan

      return self._cache[input_index][field]

   def _get_dates(self):
      dates = self._inputs[0].dates
      I = self._datesI[0]
      return np.array([dates[i] for i in I], int)

   def _get_months(self):
      months = np.unique((self.dates / 100) * 100 + 1)
      return months

   def _get_years(self):
      years = np.unique((self.dates / 10000) * 10000 + 101)
      return years

   def _get_offsets(self):
      offsets = self._inputs[0].offsets
      I = self._offsetsI[0]
      return np.array([offsets[i] for i in I], int)

   def _get_locations(self):
      locations = self._inputs[0].locations
      I = self._locationsI[0]
      use_locations = list()
      for i in I:
         use_locations.append(locations[i])
      return use_locations

   @staticmethod
   def _get_common_indices(files, name, aux=None):
      """
      Find indicies of elements that are present in all files. Merge in values
      in 'aux' as well

      Returns a list of arrays, one array for each file
      """
      # Find common values among all files
      values = aux
      for file in files:
         if(name == "Date"):
            temp = file.dates
         elif(name == "Offset"):
            temp = file.offsets
         elif(name == "Location"):
            locations = file.locations
            temp = [loc.id for loc in locations]
         if(values is None):
            values = temp
         else:
            values = np.intersect1d(values, temp)
      # Sort values, since for example, dates may not be in an ascending order
      values = np.sort(values)

      # Determine which index each value is at
      indices = list()
      for file in files:
         if(name == "Date"):
            temp = file.dates
         elif(name == "Offset"):
            temp = file.offsets
         elif(name == "Location"):
            locations = file.locations
            temp = np.zeros(len(locations))
            for i in range(0, len(locations)):
               temp[i] = locations[i].id
         I = np.where(np.in1d(temp, values))[0]
         II = np.zeros(len(I), 'int')
         for i in range(0, len(I)):
            II[i] = np.where(values[i] == temp)[0]

         indices.append(II)
      return indices

   def _get_thresholds(self):
      thresholds = None
      for file in self._inputs:
         currThresholds = file.thresholds
         if(thresholds is None):
            thresholds = currThresholds
         else:
            thresholds = set(thresholds) & set(currThresholds)

      thresholds = sorted(thresholds)
      return thresholds

   def _get_quantiles(self):
      quantiles = None
      for file in self._inputs:
         currQuantiles = file.quantiles
         if(quantiles is None):
            quantiles = currQuantiles
         else:
            quantiles = set(quantiles) & set(currQuantiles)

      quantiles = sorted(quantiles)
      return quantiles

   def _get_indices(self, axis, findex=None):
      if(axis == "date"):
         I = self._get_date_indices(findex)
      elif(axis == "offset"):
         I = self._get_offset_indices(findex)
      elif(axis == "location"):
         I = self._get_location_indices(findex)
      else:
         verif.util.error("Could not get indices for axis: " + str(axis))
      return I

   def _get_date_indices(self, input_index):
      return self._datesI[input_index]

   def _get_offset_indices(self, input_index):
      return self._offsetsI[input_index]

   def _get_location_indices(self, input_index):
      return self._locationsI[input_index]

   def _get_num_inputs(self):
      return len(self._inputs) - (self._clim is not None)

   def _get_num_inputs_with_clim(self):
      return len(self._inputs)

   def _get_variable(self):
      # TODO: Only check first file?
      return self._inputs[0].variable