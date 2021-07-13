# Organic Electrochemical Transistor (OECT) device processing

**Note: the most updated version is at my (Raj) personal site: [oect_processing](https://github.com/rajgiriUW/OECT_processing)**

This package processes sets of OECT device measurements in Python via Pandas, with primary users being the Ginger Lab at University of Washington.
It includes some simplified extraction and processing of recorded data.  
There is also simplified scripts for extracting and processing spectroelectrochemistry data.  

All pixels are expected to have a .cfg file or will auto-generate one. These files contain:
* Width and Length
* Film thickness
* Vg and Vd for output and transfer curves
* Read times (dwell time before first measurement and before each subsequent voltage step)

An example can be found in the ```tests/test_device/01/uc1_kpf6_config.cfg``` file.

## Installation/Using from Source
To install, download the source code locally and then run:

```python setup.py```

To edit locally:

```python setup.py develop```

## Using the GUI App

#### Requirements
- PyQt5
- numpy
- pyqtgraph
- pandas
- scipy
- matplotlib

#### Running the Application
1. Pull or download code from this repository.
2. In command prompt, install dependencies with
```pip install pyqt5 numpy pyqtgraph pandas scipy matplotlib```
3. Navigate to folder containing oect_processing_app.py.
4. Run the app with ```python oect_processing_app.py```

#### Usage
![application panel screenshot](https://github.com/rajgiriUW/OECT_processing/blob/master/app_panel.jpg)
1. Load parent folder(s) containing device subfolder(s), with "Load parent folder". Folders will show up in the sidebar below the button, all automatically selected for uC* plotting.
2. Deselect any device subfolders to omit from plotting by clicking on highlighted subfolders. 
3. If needed, use spinboxes to adjust device dimensions.
4. If needed, check "Average devices with the same WdL".
5. Click "Analyze" button. 
6. Once analysis is completed:
   - gm peaks for each device, uC*, and Vt values will be shown in text browser under the "Analyze" button
   - uC will be plotted on the graphs on the right, with a draggable legend on the linear graph
   - uC graphs will be automatically saved as .tif files in parent folder.
