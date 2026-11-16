
# RedLine

### A Martian pathfinder for geological exploration and rover route planning.

RedLine is a Mars exploration application designed to help users identify geological features and calculate routes to potential exploration targets.

The project focuses on the AP-1 candidate landing region in northern Amazonis Planitia, Mars. It combines geological mapping, elevation data, terrain analysis, and pathfinding to make planetary exploration more accessible.

## Features

- **Natural-language exploration:** Users can describe the geological features they want to investigate.
- **Geological feature identification:** Searches a mapped geological dataset for matching landforms.
- **Terrain analysis:** Uses elevation and slope data to identify traversable terrain.
- **A* pathfinding:** Calculates a route between a starting location and a selected geological target.
- **CTX visualization:** Displays the calculated route over high-resolution Context Camera imagery.
- **Elevation visualization:** Displays terrain elevation, geological candidates, and the calculated rover route.
- **Navigation statistics:** Reports route distance, average slope, terrain cost, and search statistics.

## Data Sources

RedLine uses planetary datasets covering the AP-1 study region.

| Dataset | Purpose |
|---|---|
| HRSC elevation model | Terrain elevation and route planning |
| HRSC slope model | Terrain traversability analysis |
| Geological GeoPackage | Geological feature identification |
| Murray Lab CTX mosaic | High-resolution route visualization |

The route-planning terrain model currently uses a 125-meter grid. CTX imagery is used for visualization and does not increase the resolution of the navigation calculations.

## Technology

- Python
- Flask
- NumPy
- GDAL / OGR / OSR
- Matplotlib
- A* pathfinding
- Geographic coordinate transformations
- HTML and CSS

## Project Structure

```text
RedLine/
├── app.py
├── RedAtlasHighRes.py
├── environment.yml
├── README.md
├── .gitignore
│
├── templates/
│   └── index.html
│
├── static/
│   └── favicon.svg
│
├── data/
│   ├── HRSC_AP1_Elevation_125m.tif
│   ├── HRSC_AP1_Slope_Clipped_125m.tif
│   ├── map-AP1.gpkg
│   └── MurrayLab_CTX_V01_E-160_N36_Mosaic.tif
│
└── outputs/
    ├── AP1_Route_Map.png
    ├── AP1_Route_CTX.png
    └── index.html
```

## Local Installation

RedLine uses a Conda environment to manage its scientific Python dependencies.

Create the environment:

```bash
conda env create -f environment.yml
```

Activate it:

```bash
conda activate redline
```

Run the Flask application:

```bash
python app.py
```

Open the local application:

```text
http://127.0.0.1:5000
```

Keep the terminal running while using the application.

## Route Calculation

The navigation engine:

1. Loads elevation and slope rasters.
2. Identifies traversable terrain using a maximum slope threshold.
3. Converts the user's starting coordinates into raster grid coordinates.
4. Matches the requested geological target to mapped features.
5. Evaluates candidate destinations using A* pathfinding.
6. Selects the lowest-cost reachable destination.
7. Generates route visualizations and navigation statistics.

Terrain movement cost increases with slope, encouraging routes across flatter terrain.

## Project Status

RedLine is an experimental planetary exploration prototype focused on geological target identification, terrain-based route planning, and visualization.

The calculated routes are based on available elevation and slope data. They are not validated rover-driving instructions and do not account for every real-world rover constraint.

The current application is designed for local execution. A production cloud deployment of the complete processing engine requires appropriate hosting for its geospatial datasets and computational workload.

## Credits

Developed as an independent planetary exploration and GIS project.

Data products and imagery are credited to their respective providers and research teams.
