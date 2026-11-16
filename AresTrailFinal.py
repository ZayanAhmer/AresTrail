import numpy as np
from osgeo import gdal, ogr, osr
import matplotlib.pyplot as plt
import heapq
import math
from pathlib import Path
import os
from dotenv import load_dotenv
from google import genai
from pydantic import BaseModel
from typing import Literal
from html import escape
from matplotlib.colors import LinearSegmentedColormap

load_dotenv()

api_key = os.getenv('GEMINI_API_KEY')

if not api_key:
    raise ValueError('Gemini API key not found.')

client = genai.Client(api_key=api_key)

print('Gemini successfully connected.')

project_dir = Path(__file__).resolve().parent

data_dir = project_dir / 'data'

elevation_path = data_dir / 'HRSC_AP1_Elevation_125m.tif'

slope_path = data_dir / 'HRSC_AP1_Slope_Clipped_125m.tif'

geology_path = data_dir / 'map-AP1.gpkg'

ctx_path = data_dir / 'CTX_AP1_Cropped_10m.tif'

output_dir = project_dir / 'outputs'
output_dir.mkdir(exist_ok=True)

cell_size = 125

max_slope = 25

slope_scale = 10

directions = [
  (-1,0), (1,0),
  (0,-1), (0,1),
  (-1,-1), (-1,1),
  (1,-1), (1,1)
]

SEARCH_PROFILES = {
  'water_ice_history': [
      'Pingo-like',
      'Polygonized terrain A',
      'Polygonized terrain B',
      'Expanded secondary crater'
  ],

  'impact_features': [
      'Expanded secondary crater'
  ],

  'polygonal_terrain': [
      'Polygonized terrain A',
      'Polygonized terrain B'
  ],

  'mound_landforms': [
      'Mound',
      'Pingo-like'
  ],

  'hummocky_terrain': [
      'Hummocky'
  ],

  'pingo_like_features': [
      'Pingo-like'
  ],

  'mounds': [
      'Mound'
  ],

  'all_landforms': [
      'Expanded secondary crater',
      'Pingo-like',
      'Mound',
      'Polygonized terrain A',
      'Polygonized terrain B',
      'Hummocky'
    ]

}



def loading_terrain():
  print('Loading Martian terrain...')

  elevation_data = gdal.Open(str(elevation_path))
  slope_data = gdal.Open(str(slope_path))

  if elevation_data is None or slope_data is None:
    raise FileNotFoundError('Error. Failed to load Martian terrain.')

  elevation = elevation_data.GetRasterBand(1).ReadAsArray()
  slope = slope_data.GetRasterBand(1).ReadAsArray()

  if elevation.shape != slope.shape:
    raise ValueError('Error. Elevation and slop grids do not match.')

  valid = np.isfinite(elevation) & np.isfinite(slope)

  elevation_no_data = elevation_data.GetRasterBand(1).GetNoDataValue()
  slope_no_data = slope_data.GetRasterBand(1).GetNoDataValue()

  if elevation_no_data is not None:
    valid &= elevation != elevation_no_data
  
  if slope_no_data is not None:
    valid &= slope != slope_no_data

  valid &= slope >= 0

  print('Martian terrain successfully loaded.')
  print('Grid Dimensions:', elevation.shape)
  print('Valid Cells:', np.count_nonzero(valid))

  return elevation, slope, valid



def calculate_terrain_cost(slope,valid):
  passable = valid & (slope<=max_slope)

  terrain_cost = np.full(slope.shape, np.inf)

  terrain_cost[passable] = 1 + (slope[passable]/slope_scale)**2

  print('Terrain cost model successfully calculated.')
  print('Traversable Cells:', np.count_nonzero(passable))
  print('Blocked Cells:', np.count_nonzero(~passable))

  return passable, terrain_cost



def get_user_start(passable):

  print('Enter the rover starting location.')

  print('202.008°-202.163° Longitude')
  print('39.677°-39.937° Latitude')

  longitude = float(input('Mars longitude (degrees east): '))
  latitude = float(input('Mars latitude (degrees): '))

  if not -90 <= latitude <= 90:
    raise ValueError('Invalid latitude.')

  raster = gdal.Open(str(elevation_path))

  if raster is None:
    raise FileNotFoundError('Could not open elevation raster.')

  if passable.shape != (raster.RasterYSize, raster.RasterXSize):
    raise ValueError('Starting grid does not match elevation raster.')

  raster_srs = osr.SpatialReference()
  raster_srs.ImportFromWkt(raster.GetProjection())

  geographic_srs = raster_srs.CloneGeogCS()

  raster_srs.SetAxisMappingStrategy(
      osr.OAMS_TRADITIONAL_GIS_ORDER
  )

  geographic_srs.SetAxisMappingStrategy(
      osr.OAMS_TRADITIONAL_GIS_ORDER
  )

  to_raster = osr.CoordinateTransformation(
      geographic_srs,
      raster_srs
  )

  to_geographic = osr.CoordinateTransformation(
      raster_srs,
      geographic_srs
  )

  transform = raster.GetGeoTransform()

  rows, cols = passable.shape

  corners = [
      (0, 0),
      (cols, 0),
      (0, rows),
      (cols, rows)
  ]

  map_longitudes = []
  map_latitudes = []

  for pixel_col, pixel_row in corners:

    x, y = gdal.ApplyGeoTransform(
        transform,
        pixel_col,
        pixel_row
    )

    point = ogr.Geometry(ogr.wkbPoint)
    point.AddPoint(x, y)
    point.Transform(to_geographic)

    map_longitudes.append(point.GetX() % 360)
    map_latitudes.append(point.GetY())

  print('Valid raster coverage:')
  print(
      'Longitude:',
      round(min(map_longitudes), 4),
      'to',
      round(max(map_longitudes), 4),
      'degrees east'
  )
  print(
      'Latitude:',
      round(min(map_latitudes), 4),
      'to',
      round(max(map_latitudes), 4),
      'degrees'
  )

  longitude = ((longitude + 180) % 360) - 180

  point = ogr.Geometry(ogr.wkbPoint)
  point.AddPoint(longitude, latitude)

  point.Transform(to_raster)

  inverse = gdal.InvGeoTransform(transform)

  if inverse is None:
      raise ValueError('Could not convert raster coordinates.')

  pixel_col, pixel_row = gdal.ApplyGeoTransform(
      inverse,
      point.GetX(),
      point.GetY()
  )

  row = math.floor(pixel_row)
  col = math.floor(pixel_col)

  if not (0 <= row < rows and 0 <= col < cols):
    raise ValueError('Starting location is outside the AP-1 map. Enter coordinates within the boundaries printed above.')

  if not passable[row, col]:

    radius = 5

    row_min = max(0, row - radius)
    row_max = min(rows, row + radius + 1)

    col_min = max(0, col - radius)
    col_max = min(cols, col + radius + 1)

    nearby = np.argwhere(
        passable[row_min:row_max, col_min:col_max]
    )

    if len(nearby) == 0:
        raise ValueError(
            'No traversable starting cell was found nearby.'
        )

    candidate_rows = nearby[:, 0] + row_min
    candidate_cols = nearby[:, 1] + col_min

    distances = np.hypot(
        candidate_rows + 0.5 - pixel_row,
        candidate_cols + 0.5 - pixel_col
    )

    best = int(np.argmin(distances))

    row = int(candidate_rows[best])
    col = int(candidate_cols[best])

    print(
        'Starting location adjusted to traversable terrain:',
        round(distances[best] * cell_size, 1),
        'meters away.'
    )

  raster = None

  start = (row, col)

  print('\nStarting location successfully converted.')
  print('Starting row:', row)
  print('Starting column:', col)

  return start

# AI/SI integration: This function uses the
# Google Gemini API to interpret natural-language
# exploration requests and identify structured targets.
def understand_user_request():

  user_request = input('What would you like to find on Mars? ')

  prompt = f"""
  You are an assistant for a Mars exploration rover.
  Your job is to understand what geological feature the user wants to investigate.
  Classify the user's request into exactly ONE key from the provided SEARCH_PROFILES dictionary below.
  Your output must be only a text saying the exact dictionary key name, NOT ITS VALUE.
  
  SEARCH_PROFILES:
  {str(SEARCH_PROFILES)}

  User request:
  {str(user_request)}
  """

  class MarsQueryResponse(BaseModel):
    target: str

  response = client.models.generate_content(
    model='gemini-3.5-flash-lite',
    contents=prompt,
    config={
        'response_mime_type': 'application/json', 'response_schema': MarsQueryResponse
      }
    )

  result = response.parsed

  if result is None:
    raise ValueError('Gemini returned an invalid response.')

  target = result.target

  print('Raw AI Category:', repr(target))

  if target == 'unknown':
    print('Sorry, I could not identify that geological target.')
    return None

  print('Understood your request.')
  print('Geological target:', target)

  return target



def find_matching_features(target):

  def normalize(value):
      return ' '.join(
          str(value)
          .strip()
          .strip('`\'"., ')
          .casefold()
          .replace('_', ' ')
          .replace('-', ' ')
          .split()
      )

  requested = normalize(target)

  for profile, features in SEARCH_PROFILES.items():

      if requested == normalize(profile):
          return features

  all_features = {
      normalize(feature): feature
      for features in SEARCH_PROFILES.values()
      for feature in features
  }

  if requested in all_features:
      return [all_features[requested]]

  aliases = {
    'mound': 'mounds',
    'mound landform': 'mound_landforms',
    'mound landforms': 'mound_landforms',
    'past water': 'water_ice_history',
    'signs of past water': 'water_ice_history',
    'impact crater': 'impact_features',
    'impact craters': 'impact_features',
    'crater': 'impact_features',
    'craters': 'impact_features',
    'polygon pattern': 'polygonal_terrain',
    'polygon patterns': 'polygonal_terrain',
    'hummocks': 'hummocky_terrain',
    'everything': 'all_landforms',
    'all features': 'all_landforms'
  }

  if requested in aliases:

      profile = aliases[requested]

      return SEARCH_PROFILES[profile]

  raise ValueError(
      f'Could not match AI result "{target}" to a geological category.'
  )



def find_geological_targets(target, passable):

  print('Searching geological map...')

  matching_features = find_matching_features(target)

  print('AI returned:', target)
  print('Searching for map features:', matching_features)
  database = ogr.Open(str(geology_path), 0)

  if database is None:
    raise FileNotFoundError('Could not open geological map.')

  layer = database.GetLayerByName('final_map')

  raster = gdal.Open(str(elevation_path))

  if layer is None or raster is None:
    raise ValueError('Could not load geological map or raster.')

  source = layer.GetSpatialRef().Clone()

  destination = osr.SpatialReference()
  destination.ImportFromWkt(raster.GetProjection())

  geographic = source.CloneGeogCS()

  source.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
  destination.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
  geographic.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)

  to_raster = osr.CoordinateTransformation(source, destination)
  to_geographic = osr.CoordinateTransformation(source, geographic)

  inverse = gdal.InvGeoTransform(raster.GetGeoTransform())

  if inverse is None:
    raise ValueError('Could not convert raster coordinates.')

  rows, cols = passable.shape

  results = []

  for feature in layer:

    feature_type = feature.GetField('Feature')

    if feature_type not in matching_features:
      continue

    geometry = feature.GetGeometryRef()

    if geometry is None:
      continue

    centroid = geometry.Centroid()

    geographic_point = centroid.Clone()

    raster_point = centroid.Clone()

    geographic_point.Transform(to_geographic)
    raster_point.Transform(to_raster)

    longitude = geographic_point.GetX() % 360
    latitude = geographic_point.GetY()

    pixel_col, pixel_row = gdal.ApplyGeoTransform(
        inverse,
        raster_point.GetX(),
        raster_point.GetY()
    )

    if not (0 <= pixel_row < rows and 0 <= pixel_col < cols):
      continue

    base_row = math.floor(pixel_row)
    base_col = math.floor(pixel_col)

    row_min = max(0, base_row - 25)
    row_max = min(rows, base_row + 26)

    col_min = max(0, base_col - 25)
    col_max = min(cols, base_col + 26)

    nearby = passable[row_min:row_max, col_min:col_max]

    available = np.argwhere(nearby)

    if len(available) == 0:
        continue

    candidate_rows = available[:, 0] + row_min
    candidate_cols = available[:, 1] + col_min

    distances = np.hypot(
        candidate_rows + 0.5 - pixel_row,
        candidate_cols + 0.5 - pixel_col
      )

    best = np.argmin(distances)

    results.append({
      'feature': feature_type,
      'feature_id': feature.GetField('id'),
      'longitude': longitude,
      'latitude': latitude,
      'row': int(candidate_rows[best]),
      'col': int(candidate_cols[best])
    })

  database = None
  raster = None

  print('Geological destinations found:', len(results))

  if not results:
    raise ValueError('No matching traversable geological destinations found.')

  return results



def choose_goal(start, locations, passable, terrain_cost):

  print('\nEvaluating possible destinations...')

  best_location = None
  best_path = None
  best_cost = float('inf')
  best_nodes = 0

  for location in locations:

    goal = (location['row'], location['col'])

    try:
      path, cost, nodes = a_star(
        passable,
        terrain_cost,
        start,
        goal
      )
    except ValueError:
      continue

    if cost < best_cost:
      best_cost = cost
      best_location = location
      best_path = path
      best_nodes = nodes

  if best_location is None:
    raise ValueError('No reachable geological destination found.')

  goal = (best_location['row'], best_location['col'])

  print('Best destination selected.')
  print('Feature:', best_location['feature'])
  print('Feature ID:', best_location['feature_id'])
  print('Mars Longitude:', best_location['longitude'])
  print('Mars Latitude:', best_location['latitude'])

  return goal, best_location, best_path, best_cost, best_nodes



def a_star(passable, terrain_cost, start, goal):
  rows, cols = passable.shape

  for name, cell in [('Start', start), ('Goal', goal)]:
    row, col = cell

    if not (0<=row<rows and 0<=col<cols):
      raise ValueError(name + ' is outside the terrain grid.')

    if not passable[row,col]:
      raise ValueError(name + ' is on impassable terrain.')

  def heuristic(row,col):
    
    distance = math.hypot(goal[0]-row,goal[1]-col)

    return distance * cell_size

  g_score = np.full(passable.shape, np.inf)
  came_from = {}

  g_score[start] = 0

  frontier = []

  heapq.heappush(
    frontier,
    (heuristic(*start), 0, start[0], start[1])
  )

  expanded_nodes = 0

  while frontier:
    estimated_cost, current_cost, row, col = heapq.heappop(frontier)

    if current_cost > g_score[row,col]:
      continue

    expanded_nodes += 1

    if (row,col) == goal:
      break

    for row_change, col_change in directions:
      next_row = row + row_change
      next_col = col + col_change

      if not (0<=next_row<rows and 0<=next_col<cols):
        continue
      if row_change != 0 and col_change != 0:
        if not passable[row,next_col] or not passable[next_row,col]:
          continue
      if not passable[next_row, next_col]:
        continue

      step_distance = cell_size * math.hypot(row_change,col_change)

      average_cost = (terrain_cost[row,col] + terrain_cost[next_row,next_col]) / 2

      movement_cost = step_distance * average_cost

      new_cost = current_cost + movement_cost

      if new_cost < g_score[next_row,next_col]:
        g_score[next_row,next_col] = new_cost

        came_from[(next_row,next_col)] = (row,col)

        f_score = new_cost + heuristic(next_row,next_col)

        heapq.heappush(
          frontier,
          (f_score,new_cost,next_row,next_col)
        )

  if not np.isfinite(g_score[goal]):
    raise ValueError('No route found between the selected points.')

  path = [goal]

  while path[-1] != start:
    previous_cell = came_from[path[-1]]

    path.append(previous_cell)

  path.reverse()

  return path, g_score[goal], expanded_nodes



def create_route_map(elevation,valid,locations,selected,path,start,goal):

  print('Creating route map...')

  fig, ax = plt.subplots(figsize=(12, 9))

  terrain = np.where(valid, elevation, np.nan)

  mars_cmap = LinearSegmentedColormap.from_list(
      'ares_trail_mars',
      [
          '#35251F',
          '#654033',
          '#98563D',
          '#C77B55',
          '#E2B18C',
          '#F5EBDD'   
      ],
      N=256
)

  image = ax.imshow(
        terrain,
        cmap=mars_cmap,
        origin='upper'
    )

  fig.colorbar(
      image,
      ax=ax,
      label='Elevation (meters)'
  )

  candidate_rows = [
      location['row'] for location in locations
  ]

  candidate_cols = [
      location['col'] for location in locations
  ]

  ax.scatter(
      candidate_cols,
      candidate_rows,
      s=35,
      marker='o',
      label='Geological candidates'
  )

  path_rows = [cell[0] for cell in path]
  path_cols = [cell[1] for cell in path]

  ax.plot(
      path_cols,
      path_rows,
      color='#F05B38',
      linewidth=2.5,
      label='Rover route'
  )

  ax.scatter(
      start[1],
      start[0],
      color='#57D6C5',
      marker='o',
      s=130,
      label='Starting location'
  )

  ax.scatter(
      goal[1],
      goal[0],
      color='#32CD32',
      marker='*',
      s=250,
      label='Selected destination'
  )

  ax.set_title(
      f"AP-1 Mars Route: {selected['feature']}"
  )

  ax.set_xlabel('Raster column')
  ax.set_ylabel('Raster row')

  ax.legend()

  fig.tight_layout()

  map_path = output_dir / 'AP1_Route_Map.png'

  fig.savefig(
      map_path,
      dpi=180,
      bbox_inches='tight'
  )

  plt.close(fig)

  print('Route map saved:', map_path)

def create_ctx_map(locations, path, selected):
  print('Creating high Rrsolution CTX map...')
  
  if not ctx_path.is_file():
    raise FileNotFoundError('CTX mosaic not found.')
  
  hrsc = gdal.Open(str(elevation_path))
  ctx = gdal.Open(str(ctx_path))

  if hrsc is None or ctx is None:
    raise ValueError('Could not open HRSC or CTX raster.')

  hrsc_srs = osr.SpatialReference()
  hrsc_srs.ImportFromWkt(hrsc.GetProjection())

  ctx_srs = osr.SpatialReference()
  ctx_srs.ImportFromWkt(ctx.GetProjection())

  if not hrsc.GetProjection() or not ctx.GetProjection():
    raise ValueError('One of the rasters has no defined CRS.')

  hrsc_srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
  ctx_srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)

  coordinate_transform = osr.CoordinateTransformation(hrsc_srs,ctx_srs)

  hrsc_transform = hrsc.GetGeoTransform()
  ctx_transform = ctx.GetGeoTransform()

  ctx_inverse = gdal.InvGeoTransform(ctx_transform)

  if ctx_inverse is None:
    raise ValueError('Could not invert CTX geotransform.')

  def convert_cell(row,col):
    x,y = gdal.ApplyGeoTransform(hrsc_transform,col+0.5,row+0.5)

    x,y,_ = coordinate_transform.TransformPoint(x,y)

    return gdal.ApplyGeoTransform(ctx_inverse, x, y)

  route = [convert_cell(r,c) for r,c in path]

  candidates = [convert_cell(item['row'], item['col']) for item in locations]

  if not all(0<=x<ctx.RasterXSize and 0<=y<ctx.RasterYSize for x, y in route):
    raise ValueError('CTX mosaic does not cover the entire route.')

  all_points = route + candidates
  padding = 150
  
  x0 = max(0, math.floor(min(p[0] for p in all_points)) - padding)
  y0 = max(0, math.floor(min(p[1] for p in all_points)) - padding)

  x1 = min(ctx.RasterXSize, math.ceil(max(p[0] for p in all_points)) + padding)
  y1 = min(ctx.RasterYSize, math.ceil(max(p[1] for p in all_points)) + padding)

  width, height = x1 - x0, y1 - y0

  scale = min(1, 1800 / max(width, height))

  band = ctx.GetRasterBand(1)

  image = band.ReadAsArray(
      x0, y0, width, height,
      max(1, int(width * scale)),
      max(1, int(height * scale))
    )

  if image is None:
    raise ValueError('Could not read CTX image.')

  mask = ~np.isfinite(image)
  
  nodata = band.GetNoDataValue()
  
  if nodata is not None:
    mask |= image == nodata

  if not np.any(~mask):
    raise ValueError('CTX image contains no valid pixels.')

  low, high = np.percentile(image[~mask], [2, 98])

  if high <= low:
    high = low + 1

  image = np.ma.array(image, mask=mask)

  fig, ax = plt.subplots(figsize=(12, 9))

  ax.imshow(
      image,
      cmap='gray',
      vmin=low,
      vmax=high,
      origin='upper',
      extent=(x0, x1, y1, y0)
    )
  
  visible_candidates = [
      (x, y) for x, y in candidates
      if x0 <= x <= x1 and y0 <= y <= y1
    ]

  if visible_candidates:
    ax.scatter(
      [p[0] for p in visible_candidates],
      [p[1] for p in visible_candidates],
      color='#F2B36D',
      s=30,
      label='Geological candidates'
    )

  ax.plot(
    [p[0] for p in route],
    [p[1] for p in route],
    color='#F05B38',
    linewidth=2.5,
    label='Rover route'
  )

  ax.scatter(*route[0], color='#57D6C5', s=110, label='Start')
  ax.scatter(*route[-1], color='#32CD32', marker='*', s=220, label='Destination')

  ax.set_title(f"AresTrail | {selected['feature']} Exploration")
  ax.set_xlabel('CTX pixel column')
  ax.set_ylabel('CTX pixel row')
  ax.legend()

  fig.tight_layout()

  map_path = output_dir / 'AP1_Route_CTX.png'

  fig.savefig(map_path, dpi=180, bbox_inches='tight')
  plt.close(fig)

  hrsc = None
  ctx = None

  print('CTX route map saved:', map_path)


  
def create_outputs_page(
  target,
  selected,
  start,
  goal,
  path,
  distance,
  total_cost,
  average_slope,
  expanded_nodes
  ):

  feature = escape(str(selected['feature']))

  feature_id_value = selected.get('feature_id')
  feature_id = (
      escape(str(feature_id_value))
      if feature_id_value is not None
      else "Not recorded"
  )

  category = escape(str(target).replace('_', ' ').title())

  page = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <link rel="icon" type="image/svg+xml" href="/static/favicon.svg">
        <title>AP-1 | AresTrail</title>
        <style>
            * {{
                box-sizing: border-box;
            }}
            body {{
                  margin: 0;
                  padding: 40px 24px;
                  background: #f7f5f0;
                  color: #292a27;
                  font-family: "Avenir Next", Avenir, "Segoe UI", sans-serif;
                  line-height: 1.7;
            }}
            main {{
                max-width: 1000px;
                margin: 0 auto;
            }}
            header {{
                padding-bottom: 20px;
                border-bottom: 1px solid #dedbd3;
                margin-bottom: 48px;
            }}
            .brand {{
                color: #b85e3c;
                font-size: 12px;
                font-weight: 700;
                letter-spacing: 1.8px;
                text-transform: uppercase;
            }}
            h1 {{
                font-family: Georgia, "Palatino Linotype", serif;
                font-size: 42px;
                font-weight: 400;
                letter-spacing: -1px;
                margin: 25px 0 8px;
                line-height: 1.2;
            }}
            h2 {{
                font-family: Georgia, "Palatino Linotype", serif;
                font-size: 23px;
                font-weight: 400;
                letter-spacing: -0.3px;
                 margin: 0;
            }}
            .subtitle {{
                color: #77766f;
                font-size: 14px;
                margin: 0;
            }}
            .accent {{
                color: #bd6746;
            }}
            .overview {{
                display: grid;
                grid-template-columns: 1.4fr 1fr 1fr;
                gap: 30px;
                padding: 25px 0;
                margin-bottom: 40px;
                border-top: 1px solid #dedbd3;
                border-bottom: 1px solid #dedbd3;
            }}
            .stat-label {{
                color: #858179;
                font-size: 11px;
                font-weight: 600;
                letter-spacing: 1px;
                text-transform: uppercase;
                margin-bottom: 7px;
            }}
            .stat-value {{
                color: #292a27;
                font-size: 22px;
                font-weight: 600;
                overflow-wrap: anywhere;
            }}
            .stat-note {{
                color: #858179;
                font-size: 12px;
                margin-top: 3px;
            }}
            .section {{
                margin-top: 42px;
            }}
            .section-heading {{
                margin-bottom: 16px;
            }}
            .section-description {{
                color: #77766f;
                font-size: 13px;
                margin-top: 4px;
            }}
            .map {{
                display: block;
                width: 100%;
                max-width: 760px;
                max-height: 460px;
                height: auto;
                object-fit: contain;
                margin: 20px auto 0;
            }}
            .details {{
                display: grid;
                grid-template-columns: 1fr 1fr;
                column-gap: 60px;
            }}
            .detail {{
                display: flex;
                justify-content: space-between;
                align-items: baseline;
                gap: 15px;
                padding: 13px 0;
                border-bottom: 1px solid #e3e0d9;
                font-size: 13px;
            }}
            .detail-label {{
                color: #77766f;
            }}
            .detail-value {{
                color: #292a27;
                font-weight: 600;
                text-align: right;
            }}
            footer {{
                margin-top: 55px;
                padding-top: 18px;
                border-top: 1px solid #dedbd3;
                display: flex;
                justify-content: space-between;
                gap: 15px;
                color: #858179;
                font-size: 11px;
            }}
            @media (max-width: 650px) {{
                body {{
                    padding: 25px 18px;
                }}
                header {{
                    margin-bottom: 30px;
                }}
                h1 {{
                    font-size: 31px;
                }}
                .overview {{
                    grid-template-columns: 1fr 1fr;
                    gap: 22px;
                }}
                .details {{
                    grid-template-columns: 1fr;
                }}
                .section {{
                    margin-top: 32px;
                }}
                footer {{
                    flex-direction: column;
                }}
            }}
            /* AresTrail visual system: warm cream, rust accents, and soft glass. */
            body {{
                min-height: 100vh;
                padding: clamp(16px, 3vw, 42px) 18px;
                background:
                    radial-gradient(ellipse at 12% 0%, rgb(255 255 255 / 82%), transparent 38%),
                    radial-gradient(ellipse at 92% 18%, rgb(224 193 171 / 28%), transparent 32%),
                    #f4eee7;
                color: #302722;
                font-family: "Avenir Next", Avenir, "Segoe UI", sans-serif;
                line-height: 1.6;
            }}
            main {{
                width: min(1500px, 100%);
                max-width: none;
            }}
            header {{
                position: relative;
                padding: 4px 0 30px;
                border-bottom: 1px solid rgb(75 53 43 / 16%);
                margin-bottom: 24px;
            }}
            .brand {{
                color: #76665c;
                font-size: 10px;
                font-weight: 700;
                letter-spacing: 2.1px;
            }}
            h1 {{
                margin: 23px 0 5px;
                color: #332720;
                font-family: Georgia, "Palatino Linotype", serif;
                font-size: clamp(44px, 7vw, 72px);
                font-weight: 400;
                letter-spacing: -2.8px;
                line-height: 1;
            }}
            h2 {{
                color: #332720;
                font-family: Georgia, "Palatino Linotype", serif;
                font-size: clamp(21px, 2.3vw, 27px);
                font-weight: 400;
                letter-spacing: -.45px;
            }}
            .subtitle {{
                color: #766d66;
                font-size: 14px;
            }}
            .accent {{
                color: #a84f34;
            }}
            .overview {{
                grid-template-columns: minmax(0, 1.4fr) repeat(2, minmax(0, 1fr));
                gap: 14px;
                padding: 0;
                margin: 0 0 30px;
                border: 0;
            }}
            .overview > div {{
                position: relative;
                overflow: hidden;
                min-width: 0;
                padding: 21px 22px;
                border: 1px solid rgb(255 255 255 / 78%);
                border-radius: 20px;
                background:
                    linear-gradient(145deg, rgb(255 255 255 / 78%), rgb(255 255 255 / 43%) 53%, rgb(234 214 199 / 32%)),
                    rgb(255 251 246 / 56%);
                box-shadow:
                    0 14px 34px rgb(72 48 35 / 8%),
                    0 1px 0 rgb(255 255 255 / 86%) inset;
                backdrop-filter: blur(18px) saturate(135%);
                -webkit-backdrop-filter: blur(18px) saturate(135%);
            }}
            .stat-label {{
                color: #806f64;
                font-size: 10px;
                letter-spacing: 1.15px;
            }}
            .stat-value {{
                color: #302722;
                font-size: clamp(19px, 2.3vw, 25px);
                font-weight: 550;
                line-height: 1.25;
            }}
            .stat-note {{
                color: #82766e;
                font-size: 11px;
            }}
            .section {{
                position: relative;
                overflow: hidden;
                margin-top: 18px;
                padding: clamp(20px, 3vw, 30px);
                border: 1px solid rgb(255 255 255 / 78%);
                border-radius: 24px;
                background:
                    linear-gradient(145deg, rgb(255 255 255 / 71%), rgb(255 255 255 / 42%) 58%, rgb(235 215 200 / 28%)),
                    rgb(255 251 246 / 46%);
                box-shadow:
                    0 16px 40px rgb(72 48 35 / 7%),
                    0 1px 0 rgb(255 255 255 / 86%) inset;
                backdrop-filter: blur(18px) saturate(130%);
                -webkit-backdrop-filter: blur(18px) saturate(130%);
            }}
            .section-heading {{
                margin-bottom: 17px;
            }}
            .section-description {{
                color: #766d66;
                font-size: 12px;
            }}
            .map {{
                width: 100%;
                max-width: none;
                max-height: none;
                margin: 0 auto;
                border: 1px solid rgb(255 255 255 / 78%);
                border-radius: 15px;
                background: #e7ded6;
                box-shadow: 0 8px 24px rgb(56 39 29 / 10%);
            }}
            .details {{
                column-gap: 12px;
                row-gap: 9px;
            }}
            .detail {{
                min-width: 0;
                padding: 13px 15px;
                border: 1px solid rgb(255 255 255 / 75%);
                border-radius: 13px;
                background: rgb(255 255 255 / 35%);
                font-size: 12px;
            }}
            .detail-label {{
                color: #766d66;
            }}
            .detail-value {{
                color: #302722;
                overflow-wrap: anywhere;
            }}
            footer {{
                margin-top: 26px;
                padding: 16px 2px 0;
                border-top: 1px solid rgb(75 53 43 / 16%);
                color: #82766e;
                font-size: 9px;
                font-weight: 650;
                letter-spacing: 1px;
            }}
            @media (max-width: 650px) {{
                body {{
                    padding: 24px 16px;
                }}
                header {{
                    padding-bottom: 22px;
                }}
                .overview {{
                    grid-template-columns: 1fr 1fr;
                }}
                .overview > div:first-child {{
                    grid-column: 1 / -1;
                }}
                .section {{
                    padding: 19px 16px;
                    border-radius: 20px;
                }}
            }}
            @media (max-width: 430px) {{
                .overview {{
                    grid-template-columns: 1fr;
                }}
                .overview > div:first-child {{
                    grid-column: auto;
                }}
                .detail {{
                    align-items: flex-start;
                }}
            }}
            .map-grid {{
                display: grid;
                grid-template-columns: repeat(2, minmax(0, 1fr));
                align-items: start;
                gap: 16px;
            }}
            .map-card {{
                min-width: 0;
                padding: 16px;
                border: 1px solid rgb(255 255 255 / 74%);
                border-radius: 17px;
                background: rgb(255 255 255 / 25%);
            }}
            .map-card h3 {{
                margin: 0;
                color: #332720;
                font-family: Georgia, "Palatino Linotype", serif;
                font-size: 19px;
                font-weight: 400;
                letter-spacing: -.25px;
            }}
            .map-card .section-description {{
                min-height: 38px;
                margin: 4px 0 13px;
            }}
            .map-card .map {{
                display: block;
                width: 100%;
                height: auto;
                max-height: none;
                margin: 0;
                object-fit: contain;
            }}
            @media (max-width: 650px) {{
                .map-grid {{
                    grid-template-columns: 1fr;
                }}
                .map-card .section-description {{
                    min-height: 0;
                }}
                .map-card .map {{
                    height: auto;
                    max-height: none;
                }}
            }}
        </style>
    </head>
    <body>
    <main>
        <header>
            <div class="brand">AresTrail &nbsp; / &nbsp; AP-1 EXPEDITION</div>
            <h1>
                <strong>AresTrail</strong>
            </h1>
            <p class="subtitle">
                Route results for your expedition across North Amazonis Planitia.
            </p>
        </header>
        <section class="overview">
            <div>
                <div class="stat-label">Selected Feature</div>
                <div class="stat-value">{feature}</div>
                <div class="stat-note">AI search: {category}</div>
            </div>
            <div>
                <div class="stat-label">Route Distance</div>
                <div class="stat-value">{distance / 1000:.2f} km</div>
                <div class="stat-note">Calculated travel distance</div>
            </div>
            <div>
                <div class="stat-label">Average Slope</div>
                <div class="stat-value">{average_slope:.2f}°</div>
                <div class="stat-note">Along the selected route</div>
            </div>
        </section>
        <section class="section">
            <div class="section-heading">
                <h2>Mission Maps</h2>
            </div>
            <div class="map-grid">
                <article class="map-card">
                    <h3>Calculated Rover Route</h3>
                    <div class="section-description">
                        Elevation model, geological candidates, and A* navigation path.
                    </div>
                    <img
                        class="map"
                        src="/results/AP1_Route_CTX.png"
                        alt="AP-1 elevation map showing the calculated rover route"
                    >
                </article>
                <article class="map-card">
                    <h3>Elevation Analysis</h3>
                    <div class="section-description">HRSC elevation analysis</div>
                    <img
                        class="map"
                        src="/results/AP1_Route_Map.png"
                        alt="HRSC elevation analysis"
                    >
                </article>
            </div>
        </section>
        <section class="section">
            <div class="section-heading">
                <h2>Destination &amp; Coordinates</h2>
            </div>
            <div class="details">
                <div class="detail">
                    <span class="detail-label">Feature ID</span>
                    <span class="detail-value">{feature_id}</span>
                </div>
                <div class="detail">
                    <span class="detail-label">Mars Longitude</span>
                    <span class="detail-value">
                        {selected['longitude']:.5f}° E
                    </span>
                </div>
                <div class="detail">
                    <span class="detail-label">Mars Latitude</span>
                    <span class="detail-value">
                        {selected['latitude']:.5f}°
                    </span>
                </div>
                <div class="detail">
                    <span class="detail-label">Starting Cell</span>
                    <span class="detail-value">{start}</span>
                </div>
                <div class="detail">
                    <span class="detail-label">Destination Cell</span>
                    <span class="detail-value">{goal}</span>
                </div>
                <div class="detail">
                    <span class="detail-label">Route Cells</span>
                    <span class="detail-value">{len(path)}</span>
                </div>
            </div>
        </section>
        <section class="section">
            <div class="section-heading">
                <h2>Navigation Statistics</h2>
            </div>
            <div class="details">
                <div class="detail">
                    <span class="detail-label">Physical Distance</span>
                    <span class="detail-value">
                        {distance:,.2f} meters
                    </span>
                </div>
                <div class="detail">
                    <span class="detail-label">Weighted Terrain Cost (Relatively safe path if similar in value to distance)</span>
                    <span class="detail-value">
                        {total_cost:,.2f}
                    </span>
                </div>
                <div class="detail">
                    <span class="detail-label">Average Slope</span>
                    <span class="detail-value">
                        {average_slope:.2f} degrees
                    </span>
                </div>
                <div class="detail">
                    <span class="detail-label">Nodes Investigated</span>
                    <span class="detail-value">
                        {expanded_nodes:,}
                    </span>
                </div>
            </div>
        </section>
        <footer>
            <span>AP-1 MARTIAN GEOLOGICAL NAVIGATION</span>
            <span>HRSC TERRAIN DATA &nbsp; / &nbsp; 125 M RESOLUTION</span>
        </footer>
    </main>
    </body>
    </html>
    """
  
  page_path = output_dir / 'index.html'

  with open(page_path, 'w', encoding='utf-8') as file:
    file.write(page)

  print('HTML results page saved:', page_path)



if __name__ == '__main__':
  elevation, slope, valid = loading_terrain()
  passable, terrain_cost = calculate_terrain_cost(slope, valid)


  print()
  print()
  print('Welcome to AresTrail, the martian pathfinder which turns natural-language objectives into algorithmic data and routes. Follow the instructions and watch the program build a path catered to your needs.')
  print('Currently, the map focuses on the promising future landing site of Northern Amazonis Planitia, specifically AP-1.')
  print()
  print()

  start = get_user_start(passable)

  target = understand_user_request()

  if target is None:
    raise SystemExit('No geological target identified.')

  locations = find_geological_targets(target,passable)

  goal, selected, path, total_cost, expanded_nodes = choose_goal(start,locations,passable,terrain_cost)

  distance = 0

  for i in range(1, len(path)):

    previous = path[i - 1]
    current = path[i]

    distance += cell_size * math.hypot(
      current[0] - previous[0],
      current[1] - previous[1]
    )

  route_slopes = np.array([
      slope[row, col] for row, col in path
    ])

  average_slope = np.mean(route_slopes)

  create_route_map(
      elevation,
      valid,
      locations,
      selected,
      path,
      start,
      goal
  )
  
  create_ctx_map(
      locations,
      path,
      selected
  )
  create_outputs_page(
      target,
      selected,
      start,
      goal,
      path,
      distance,
      total_cost,
      average_slope,
      expanded_nodes
  )

  print('Opening results page...')

  print('AI Geological Category:', target)
  print('Mapped Feature:', selected['feature'])
  print('Feature ID:', selected['feature_id'])
  print('Mars Longitude:', selected['longitude'])
  print('Mars Latitude:', selected['latitude'])
  print('Starting Cell:', start)
  print('Destination Cell:', goal)
  print('Route Cells:', len(path))
  print('Physical Distance:', round(distance, 2), 'meters')
  print('Weighted Terrain Cost:', round(total_cost, 2))
  print('Average Slope:', round(average_slope, 2), 'degrees')
  print('Nodes Investigated:', expanded_nodes)
  print('\nRoute successfully calculated.')