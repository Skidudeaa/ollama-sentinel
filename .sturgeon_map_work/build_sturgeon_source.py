#!/usr/bin/env python3
from __future__ import annotations
import csv, hashlib, html, io, json, math, mimetypes, re, shutil, time, zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from shapely.geometry import GeometryCollection, mapping, shape
from shapely.ops import unary_union
from shapely.validation import make_valid

ID='15-6600-55380'; LEGACY='5QA0911'; NAME='Sturgeon Lake'; PKG='09'
ROOT=Path(__file__).resolve().parent; OUT=ROOT/'output'; WORK=ROOT/'work'; RAW=WORK/'raw'; HIST=WORK/'historical'; META=WORK/'metadata'
S={
'index':'https://ws.lioservices.lrc.gov.on.ca/arcgis2/rest/services/LIO_OPEN_DATA/LIO_Open01/MapServer/31',
'line':'https://ws.lioservices.lrc.gov.on.ca/arcgis2/rest/services/LIO_OPEN_DATA/LIO_Open01/MapServer/30',
'point':'https://ws.lioservices.lrc.gov.on.ca/arcgis2/rest/services/LIO_OPEN_DATA/LIO_Open01/MapServer/27',
'waterbody':'https://ws.lioservices.lrc.gov.on.ca/arcgis2/rest/services/LIO_OPEN_DATA/LIO_Open01/MapServer/25',
'historic':'https://services1.arcgis.com/TJH5KDher0W13Kgo/arcgis/rest/services/Historic_Bathymetry_Index/FeatureServer/0'}
PKG_URL=f'https://ws.gisetl.lrc.gov.on.ca/fmedatadownload/Packages/HistoricBathymetryMaps-{PKG}.zip'
BSM='https://raw.githubusercontent.com/ross-alex/docLandscape/ae055365bcf373156716aeca0dc5c401eac0f0dc/data/cyc3_lakesBathy.csv'
R=requests.Session(); R.headers['User-Agent']='SturgeonLakeFieldMap/1.0'
R.mount('https://',HTTPAdapter(max_retries=Retry(total=5,backoff_factor=2,status_forcelist=[429,500,502,503,504],allowed_methods=['GET'])))
EV=[]
def now(): return datetime.now(timezone.utc).isoformat().replace('+00:00','Z')
def log(msg,**kw):
 d={'time':now(),'message':msg,**kw}; EV.append(d); print(json.dumps(d),flush=True)
def dump(p,x): p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps(x,indent=2,ensure_ascii=False,default=str)+'\n')
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1048576),b''): h.update(b)
 return h.hexdigest()
def get(url,params=None,stream=False,timeout=300):
 r=R.get(url,params=params,stream=stream,timeout=(30,timeout)); r.raise_for_status(); return r
def js(url,params=None):
 r=get(url,params,timeout=180); d=r.json()
 if isinstance(d,dict) and d.get('error'): raise RuntimeError(d['error'])
 return d
def info(k):
 d=js(S[k],{'f':'pjson'}); dump(META/f'{k}_layer.json',d); return d
def fields(i): return {str(x.get('name','')).upper() for x in i.get('fields',[])}
def q(layer,inf,where='1=1',geom=None):
 p={'where':where,'returnIdsOnly':'true','f':'json'}
 if geom:p.update(geometry=json.dumps(geom,separators=(',',':')),geometryType='esriGeometryEnvelope',inSR='4326',spatialRel='esriSpatialRelIntersects')
 ids=sorted(js(layer+'/query',p).get('objectIds') or []); log('ids',layer=layer,count=len(ids),where=where)
 out=[]; n=min(int(inf.get('maxRecordCount') or 1000),1000)
 for a in range(0,len(ids),n):
  d=js(layer+'/query',{'objectIds':','.join(map(str,ids[a:a+n])),'outFields':'*','returnGeometry':'true','outSR':'4326','f':'geojson'})
  out.extend(d.get('features') or [])
 return out
def geo(p,fs,name): dump(p,{'type':'FeatureCollection','name':name,'crs':{'type':'name','properties':{'name':'urn:ogc:def:crs:OGC:1.3:CRS84'}},'features':fs})
def vgeom(g):
 if not g:return None
 x=shape(g)
 if x.is_empty:return None
 return make_valid(x) if not x.is_valid else x
def poly(fs):
 gs=[vgeom(f.get('geometry')) for f in fs]; gs=[g for g in gs if g and g.geom_type in ('Polygon','MultiPolygon')]
 return unary_union(gs) if gs else None
def clip(fs,lake,kind):
 out=[]; qa={'input':len(fs),'outside':0,'invalid':0,'output':0}
 for f in fs:
  try:
   g=vgeom(f.get('geometry'))
   if not g:qa['invalid']+=1;continue
   if kind=='point':
    if not lake.covers(g):qa['outside']+=1;continue
   else:
    if not lake.intersects(g):qa['outside']+=1;continue
    g=g.intersection(lake)
    if g.geom_type=='GeometryCollection':
     z=[x for x in g.geoms if x.geom_type in ('LineString','MultiLineString')]; g=unary_union(z) if z else GeometryCollection()
    if g.is_empty or g.geom_type not in ('LineString','MultiLineString'):qa['outside']+=1;continue
   out.append({'type':'Feature','id':f.get('id'),'properties':f.get('properties') or {},'geometry':mapping(g)})
  except Exception:qa['invalid']+=1
 qa['output']=len(out);return out,qa
def depth(fs):
 z=[]
 for f in fs:
  for k,v in (f.get('properties') or {}).items():
   if k.upper()=='DEPTH':
    try:
     x=float(v)
     if math.isfinite(x):z.append(x)
    except:pass
 return z
def urls(x):
 s=html.unescape(str(x or '')); z=set(re.findall(r"href\s*=\s*['\"]([^'\"]+)",s,re.I)); z.update(re.findall(r'https?://[^\s\'\"<>]+',s,re.I)); return [u.rstrip('),.;') for u in z if u.startswith('http')]
def fname(r,u,i):
 cd=r.headers.get('content-disposition',''); m=re.search(r"filename\*=UTF-8''([^;]+)",cd,re.I) or re.search(r"filename=['\"]?([^;'\"]+)",cd,re.I)
 n=unquote(m.group(1)) if m else Path(urlparse(r.url or u).path).name
 n=re.sub(r'[^A-Za-z0-9._-]+','_',n or f'historic_{i}').strip('._') or f'historic_{i}'
 if '.' not in n:n+=mimetypes.guess_extension(r.headers.get('content-type','').split(';')[0]) or '.bin'
 return n
def direct(u,i,seen=None):
 seen=seen or set()
 if u in seen:return []
 seen.add(u); r=get(u,stream=True,timeout=600); ct=r.headers.get('content-type','').lower()
 if 'text/html' in ct:
  b=r.content[:10000000]; t=b.decode(r.encoding or 'utf8','replace'); out=[]
  for j,v in enumerate(urls(t)):
   a=urljoin(r.url,v)
   if any(e in a.lower() for e in ('.jpg','.jpeg','.png','.pdf','.tif','.tiff','.zip')):
    try:out+=direct(a,i*100+j,seen)
    except Exception as e:log('historic_child_failed',url=a,error=repr(e))
  return out
 p=HIST/fname(r,u,i); c=0
 with p.open('wb') as f:
  for b in r.iter_content(1048576):
   if b:c+=len(b);f.write(b)
 log('historic_download',url=u,file=p.name,bytes=c);return[p]
def package():
 p=WORK/f'HistoricBathymetryMaps-{PKG}.zip'; r=get(PKG_URL,stream=True,timeout=1800); c=0
 with p.open('wb') as f:
  for b in r.iter_content(1048576):
   if b:c+=len(b);f.write(b)
 tok=[re.sub('[^A-Z0-9]','',LEGACY),re.sub('[^A-Z0-9]','',ID)] ;out=[]
 with zipfile.ZipFile(p) as z:
  names=z.namelist(); match=[n for n in names if any(t in re.sub('[^A-Z0-9]','',n.upper()) for t in tok)]
  dump(META/'historic_package_match.json',{'members':len(names),'matches':match})
  for n in match:
   if n.endswith('/') or Path(n).suffix.lower() not in {'.jpg','.jpeg','.png','.pdf','.tif','.tiff','.tfw','.jgw','.pgw','.xml','.txt'}:continue
   x=HIST/Path(n).name
   with z.open(n) as a,x.open('wb') as b:shutil.copyfileobj(a,b)
   out.append(x)
 p.unlink(missing_ok=True);log('package_extract',count=len(out),bytes=c);return out
def main():
 for p in (OUT,WORK):
  if p.exists():shutil.rmtree(p)
 for p in (OUT,RAW,HIST,META):p.mkdir(parents=True,exist_ok=True)
 I={k:info(k) for k in S}
 idx=q(S['index'],I['index'],f"WBY_LID='{ID}'"); his=q(S['historic'],I['historic'],f"WBY_LID='{ID}'")
 try:wat=q(S['waterbody'],I['waterbody'],f"WBY_LID='{ID}'")
 except Exception as e:log('waterbody_exact_failed',error=repr(e));wat=[]
 geo(RAW/'bathymetry_index.geojson',idx,'Bathymetry Index');geo(RAW/'historic_index.geojson',his,'Historic Index');geo(RAW/'ohn_waterbody.geojson',wat,'OHN Waterbody')
 lake=poly(wat)
 if lake is None:lake=poly(idx)
 if lake is None:lake=poly(his)
 if lake is None:raise RuntimeError('No exact lake polygon')
 minx,miny,maxx,maxy=lake.bounds; env={'xmin':minx-.003,'ymin':miny-.003,'xmax':maxx+.003,'ymax':maxy+.003,'spatialReference':{'wkid':4326}}
 def layer(k):
  if 'WBY_LID' in fields(I[k]):
   try:
    x=q(S[k],I[k],f"WBY_LID='{ID}'")
    if x:return x,'exact WBY_LID'
   except Exception as e:log('exact_failed',layer=k,error=repr(e))
  return q(S[k],I[k],'1=1',env),'spatial envelope'
 lr,lq=layer('line');pr,pq=layer('point'); lines,lqa=clip(lr,lake,'line');points,pqa=clip(pr,lake,'point')
 geo(RAW/'bathymetry_lines_raw.geojson',lr,'Bathymetry Lines Raw');geo(RAW/'bathymetry_points_raw.geojson',pr,'Bathymetry Points Raw');geo(RAW/'bathymetry_lines_clipped.geojson',lines,'Bathymetry Lines Clipped');geo(RAW/'bathymetry_points_clipped.geojson',points,'Bathymetry Points Clipped');geo(RAW/'lake_boundary.geojson',[{'type':'Feature','properties':{'WBY_LID':ID,'name':NAME,'source':'OHN Waterbody' if wat else 'Bathymetry Index'},'geometry':mapping(lake)}],'Lake Boundary')
 hu=[]
 for f in his:
  for k,v in (f.get('properties') or {}).items():
   if any(x in k.upper() for x in ('DOWNLOAD','URL','LINK')):hu+=urls(v)
 hu=list(dict.fromkeys(hu));dump(META/'historic_urls.json',hu);sc=[]
 for i,u in enumerate(hu,1):
  try:sc+=direct(u,i)
  except Exception as e:log('historic_direct_failed',url=u,error=repr(e))
 if not sc:
  try:sc=package()
  except Exception as e:log('historic_package_failed',error=repr(e))
 bsm=None
 try:
  for row in csv.DictReader(io.StringIO(get(BSM,timeout=180).text)):
   if row.get('waterbodyID')==ID:bsm=row;break
 except Exception as e:log('bsm_failed',error=repr(e))
 if bsm:dump(META/'broad_scale_monitoring_record.json',bsm)
 ld=depth(lines);pd=depth(points);ad=ld+pd;bm=float(bsm['maxDepth']) if bsm and bsm.get('maxDepth') else None;mx=max(ad) if ad else None
 units='metres (validated against 2019 BSM maximum)' if mx is not None and bm is not None and abs(mx-bm)<=1 else 'UNVERIFIED'
 summ={'lake':{'name':NAME,'waterbody_id':ID,'legacy_code':LEGACY,'bbox':list(lake.bounds),'centroid':[lake.centroid.x,lake.centroid.y]},'queries':{'line':lq,'point':pq},'counts':{'index':len(idx),'historic_index':len(his),'waterbody':len(wat),'lines_raw':len(lr),'lines_clipped':len(lines),'points_raw':len(pr),'points_clipped':len(points),'historic_files':len(sc)},'qa':{'lines':lqa,'points':pqa},'depth':{'line_count':len(ld),'point_count':len(pd),'min':min(ad) if ad else None,'max':mx,'units':units,'bsm_max_depth_m':bm,'line_values':sorted(set(ld))},'bsm':bsm,'historic_files':[{'file':p.name,'bytes':p.stat().st_size,'sha256':sha(p)} for p in sc],'retrieved_utc':now()}
 dump(WORK/'summary.json',summ);dump(WORK/'PROVENANCE.json',{'created_utc':now(),'lake':{'WBY_LID':ID,'legacy_code':LEGACY},'licence':'Open Government Licence – Ontario','services':S,'historic_package':PKG_URL,'bsm_source':BSM,'processing':['Exact WBY_LID queries where supported','Spatial fallback by exact lake envelope','Lines clipped to official lake polygon','Points filtered to official lake polygon','No proprietary chart data copied'],'events':EV})
 (WORK/'README.md').write_text(f'# {NAME} official source bundle\n\nWaterbody ID `{ID}`; legacy code `{LEGACY}`. Ontario official contour, sounding, index, waterbody and historic-map data. Historic bathymetry is not a navigation chart.\n')
 z=OUT/'Sturgeon_Lake_official_source_bundle.zip'
 with zipfile.ZipFile(z,'w',zipfile.ZIP_DEFLATED,allowZip64=True) as a:
  for p in sorted(WORK.rglob('*')):
   if p.is_file():a.write(p,p.relative_to(WORK))
 dump(OUT/'build_status.json',{'status':'success','bundle':z.name,'bytes':z.stat().st_size,'sha256':sha(z),'summary':summ});log('complete',bytes=z.stat().st_size,sha256=sha(z))
if __name__=='__main__':main()
