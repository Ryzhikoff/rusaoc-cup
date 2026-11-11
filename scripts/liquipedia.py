#!/usr/bin/env python3
"""Update the standalone Rusaoc Cup page from Liquipedia's public API.

Run: python3 update-rusaoc-liquipedia.py
Refresh only flags in both pages: python3 update-rusaoc-liquipedia.py --flags-only
Uses only Python's standard library. Both HTML files are kept if any API call fails.
"""
import json, re, collections, html, datetime, time, gzip, sys, argparse
from pathlib import Path
from urllib.parse import urlencode, quote
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

PAGES = {}
ALIASES={'i an ta go ni st i':'AntagonisT','iantagonisti':'AntagonisT','daniil':'DzeDanik','daniil dz':'DzeDanik'}
PLACEHOLDERS={'','tbd','bye','none','unknown','?','to be determined','—','-'}
def strip_comments(s):return re.sub(r'<!--[\s\S]*?-->','',s)
def read_tpl(s,start):
 depth=0;i=start
 while i<len(s)-1:
  if s[i:i+2]=='{{':depth+=1;i+=2;continue
  if s[i:i+2]=='}}':
   depth-=1;i+=2
   if depth==0:return s[start:i]
   continue
  i+=1
 raise ValueError('unclosed template')
def split_parts(t):
 s=t[2:-2];out=[];start=0;i=0;depth=0;link=0
 while i<len(s):
  if s[i:i+2]=='{{':depth+=1;i+=2;continue
  if s[i:i+2]=='}}':depth-=1;i+=2;continue
  if s[i:i+2]=='[[':link+=1;i+=2;continue
  if s[i:i+2]==']]':link-=1;i+=2;continue
  if s[i]=='|' and depth==0 and link==0:out.append(s[start:i]);start=i+1
  i+=1
 out.append(s[start:]);return out

def parse_tpl(t):
 parts=split_parts(t);name=parts[0].strip();fields={};pos=[]
 for item in parts[1:]:
  if '=' in item and not item.strip().startswith('{{'):
   k,v=item.split('=',1);fields[k.strip().lower()]=v.strip()
  else:pos.append(item.strip())
 return name,fields,pos

def clean_name(x):
 x=strip_comments(x).strip();x=html.unescape(x)
 x=re.sub(r'\[\[([^\]]+)\]\]',lambda m:m.group(1).split('|')[-1],x)
 x=re.sub(r'<[^>]+>','',x).strip().strip('"').strip("'")
 x=re.sub(r'\s+',' ',x)
 if x.casefold() in PLACEHOLDERS or '{{' in x or '}}' in x:return ''
 return x

def key(x):return re.sub(r'[\s_]+',' ',clean_name(x)).casefold()
def canon(x):
 x=clean_name(x);return ALIASES.get(key(x),x)
def names_from_opponent(t,teams):
 name,fields,pos=parse_tpl(t);lower=name.casefold()
 if lower in ('soloopp','soloopponent'):
  return [canon(fields.get('name') or fields.get('player') or (pos[0] if pos else ''))]
 if lower in ('duoopponent','trioopponent'):
  keyed=[(int(k[1:]),canon(v)) for k,v in fields.items() if re.fullmatch(r'p[123]',k)]
  return [v for _,v in sorted(keyed)] if keyed else [canon(v) for v in pos[:(2 if lower.startswith('duo') else 3)]]
 if lower in ('teamopponent','opponent','literalopponent'):
  keyed=[(int(k[1:]),canon(v)) for k,v in fields.items() if re.fullmatch(r'p[123]',k)]
  if keyed:return [v for _,v in sorted(keyed)]
  team=fields.get('template') or fields.get('name') or (pos[0] if pos else '')
  return teams.get(key(team),[]) if lower=='teamopponent' else ([canon(team)] if team else [])
 return []

def nested(t,name_pattern):
 for m in re.finditer(r'\{\{('+name_pattern+r')(?=[|}\s])',t,re.I):
  yield read_tpl(t,m.start())

def team_rosters(s):
 teams={}
 for t in nested(s,'TeamCard'):
  _,f,_=parse_tpl(t);name=clean_name(f.get('team',''))
  people=[canon(v) for k,v in sorted(f.items()) if re.fullmatch(r'p[1234]',k) and canon(v)]
  if name and people:teams[key(name)]=people
 return teams

def participants(s,teams):
 people=set()
 m=re.search(r'^==Participants==([\s\S]*?)(?=^==[^=]|\Z)',s,re.M)
 if not m:return people
 section=m.group(1)
 for t in nested(section,'(?:ParticipantSection|ParticipantTable|TeamCard|SoloOpponent|DuoOpponent|TrioOpponent)'):
  name,f,pos=parse_tpl(t)
  if name.casefold() in ('soloopp','soloopponent','duoopponent','trioopponent'):
   people.update(v for v in names_from_opponent(t,teams) if v)
  else:
   people.update(canon(v) for k,v in f.items() if re.fullmatch(r'p\d+',k) and canon(v))
 return people

def matches(s,teams):
 stats=collections.defaultdict(lambda:[0,0,0]);people=set();unresolved=0;score_only=0;map_based=0;match_count=0;walkovers=0;unknown_scores=0
 for t in nested(s,'Match'):
  name,f,_=parse_tpl(t);match_count+=1
  sides=[];scores=[]
  for n in (1,2):
   raw=f.get('opponent'+str(n),'')
   if raw.startswith('{{'):
    ot=read_tpl(raw,0);players=[v for v in names_from_opponent(ot,teams) if v];_,of,_=parse_tpl(ot);score=of.get('score','')
   else:players=[];score=''
   sides.append(players);scores.append(int(score) if score.isdigit() else None);people.update(players)
  mapwins=[0,0];map_count=0
  for raw in f.values():
   if raw.startswith('{{Map'):
    _,mf,_=parse_tpl(read_tpl(raw,0));w=mf.get('winner','')
    if w in ('1','2'):mapwins[int(w)-1]+=1;map_count+=1
  walkover=bool(f.get('walkover','').strip()) or any(re.search(r'\b(?:FF|W)\b',f.get('opponent'+str(n),'')) for n in (1,2))
  if walkover:walkovers+=1
  if map_count:map_based+=1
  if all(v is not None for v in scores) and not walkover:
   if all(scores[i]>=mapwins[i] for i in (0,1)):
    if not map_count and sum(scores):score_only+=1
    final=scores
   else:final=mapwins;unknown_scores+=1
  else:final=mapwins
  if sum(final)>0 and any(not side for side in sides):unresolved+=1
  if sum(final)==0 and f.get('winner') in ('1','2') and not walkover:unknown_scores+=1
  for i in (0,1):
   for p in sides[i]:
    stats[p][0]+=final[i];stats[p][1]+=final[1-i];stats[p][2]+=sum(final)
 return stats,people,{'matches':match_count,'mapMatches':map_based,'scoreMatches':score_only,'unresolved':unresolved,'walkovers':walkovers,'unknownScores':unknown_scores}

def winner_from_prize(s,teams):
 # Older prize pools mark the first-place slot directly.
 for t in nested(s,r'(?:prize pool slot(?: team|_team)?)'):
  name,f,pos=parse_tpl(t)
  if f.get('place','').strip()!='1':continue
  for x in pos:
   if x.startswith('{{'):
    for pt in nested(x,'(?:SoloOpponent|DuoOpponent|TrioOpponent|TeamOpponent|Opponent)'):
     people=[v for v in names_from_opponent(pt,teams) if v]
     if people:return people
   else:
    people=teams.get(key(x))
    if people:return people
    if ' & ' in x:
     people=[canon(v) for v in x.split(' & ') if canon(v)]
     if len(people) in (2,3):return people
    team_stem=re.sub(r"(?i)'s\s+team$",'',x).strip()
    if team_stem!=x:
     people=teams.get(key(team_stem))
     if people:return people
     stem=canon(team_stem)
     if stem:return [stem]
    person=canon(x)
    if person:return [person]
 # Newer prize pools contain nested Slot templates; the first one is first place.
 for pool in nested(s,'(?:SoloPrizePool|DuoPrizePool|TrioPrizePool|TeamPrizePool)'):
  for slot in nested(pool,'Slot'):
   for pt in nested(slot,'(?:SoloOpponent|DuoOpponent|TrioOpponent|TeamOpponent|Opponent)'):
    people=[v for v in names_from_opponent(pt,teams) if v]
    if people:return people
   break
 return []

def winner_from_final(raw,teams):
 for label in ['Grand Final','Final']:
  m=re.search(r'<!--\s*'+label+r'\s*-->',raw,re.I)
  if not m:continue
  nextmatch=re.search(r'\{\{Match\b',raw[m.end():],re.I)
  if not nextmatch:continue
  t=read_tpl(raw,m.end()+nextmatch.start());_,f,_=parse_tpl(t)
  raw_sides=[f.get('opponent1',''),f.get('opponent2','')]
  scores=[];sides=[]
  for x in raw_sides:
   if x.startswith('{{'):
    o=read_tpl(x,0);_,of,_=parse_tpl(o);scores.append(int(of['score']) if of.get('score','').isdigit() else 0);sides.append([v for v in names_from_opponent(o,teams) if v])
   else:scores.append(0);sides.append([])
  win=f.get('winner','')
  if win in ('1','2') and sides[int(win)-1]:return sides[int(win)-1]
  if scores[0]!=scores[1] and sides[0 if scores[0]>scores[1] else 1]:return sides[0 if scores[0]>scores[1] else 1]
  mapwins=[0,0]
  for mt in nested(t,'Map'):
   _,mf,_=parse_tpl(mt);w=mf.get('winner','')
   if w in ('1','2'):mapwins[int(w)-1]+=1
  if mapwins[0]!=mapwins[1] and sides[0 if mapwins[0]>mapwins[1] else 1]:return sides[0 if mapwins[0]>mapwins[1] else 1]
 return []

def parse_page(title,raw):
 cup=int(title.split('/')[-1]);s=strip_comments(raw);teams=team_rosters(s)
 infobox=next(nested(s,'Infobox league'),None)
 _,info,_=parse_tpl(infobox) if infobox else ('',{},[])
 format_value=info.get('format','')
 mode=re.search(r'(?i)\b(1v1|2v2|3v3|FFA)\b',format_value)
 if not mode:
  mode=re.search(r'(?i)\b(1v1|2v2|3v3|FFA)\b',s[:1000])
 mode=mode.group(1).upper() if mode and mode.group(1).upper()=='FFA' else mode.group(1) if mode else '?'
 if info.get('team_number') and mode=='1v1':
  sizes=[len(v) for v in teams.values() if len(v) in (2,3)]
  if sizes:mode=str(collections.Counter(sizes).most_common(1)[0][0])+'v'+str(collections.Counter(sizes).most_common(1)[0][0])
 stats,match_people,quality=matches(s,teams)
 people=participants(s,teams)|match_people
 champions=winner_from_final(raw,teams) or winner_from_prize(s,teams)
 people.update(champions)
 declared=info.get('player_number') or info.get('team_number') or ''
 return {'cup':cup,'mode':mode,'date':info.get('date',''),'declared':int(declared) if declared.isdigit() else None,'participants':sorted(people,key=str.casefold),'champions':champions,'stats':dict(stats),'quality':quality,'source':'https://liquipedia.net/ageofempires/Rusaoc_Cup/'+str(cup)}


API = "https://liquipedia.net/ageofempires/api.php"
OUTPUT = Path(__file__).resolve().parents[1] / "site" / "rusaoc-cup-liquipedia.html"
SHEET_OUTPUT = OUTPUT.with_name("rusaoc-cup-statistics.html")
USER_AGENT = "RusaocCupStatistics/1.1 (https://github.com/Ryzhikoff/rusaoc-cup; daily archive update)"

# Only ISO country codes can become flags. Region/team codes (CIS, EU, etc.)
# and unrecognized values stay unknown rather than inheriting the event's country.
COUNTRY_CODES = set("AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE JM JO JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW PY QA RE RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW".split())
COUNTRY_ALIASES = {
    "russia": "RU", "rus": "RU", "ukraine": "UA", "ukr": "UA",
    "belarus": "BY", "blr": "BY", "kazakhstan": "KZ", "kaz": "KZ",
    "united states": "US", "united states of america": "US", "usa": "US",
    "united kingdom": "GB", "uk": "GB", "great britain": "GB",
    "armenia": "AM", "argentina": "AR", "australia": "AU", "austria": "AT",
    "azerbaijan": "AZ", "belgium": "BE", "bel": "BE", "brazil": "BR",
    "bulgaria": "BG", "canada": "CA", "chile": "CL", "china": "CN",
    "colombia": "CO", "croatia": "HR", "czech republic": "CZ", "czechia": "CZ",
    "denmark": "DK", "estonia": "EE", "finland": "FI", "france": "FR",
    "georgia": "GE", "germany": "DE", "greece": "GR", "hong kong": "HK",
    "hungary": "HU", "iceland": "IS", "india": "IN", "indonesia": "ID",
    "ireland": "IE", "israel": "IL", "italy": "IT", "japan": "JP",
    "kyrgyzstan": "KG", "latvia": "LV", "lithuania": "LT", "malaysia": "MY",
    "mexico": "MX", "moldova": "MD", "mongolia": "MN", "netherlands": "NL",
    "new zealand": "NZ", "norway": "NO", "peru": "PE", "philippines": "PH",
    "poland": "PL", "portugal": "PT", "romania": "RO", "serbia": "RS",
    "singapore": "SG", "slovakia": "SK", "slovenia": "SI", "south africa": "ZA",
    "south korea": "KR", "spain": "ES", "sweden": "SE", "switzerland": "CH",
    "taiwan": "TW", "tajikistan": "TJ", "thailand": "TH", "turkey": "TR",
    "türkiye": "TR", "turkmenistan": "TM", "uruguay": "UY", "uzbekistan": "UZ",
    "venezuela": "VE", "vietnam": "VN",
}


def country_code(value):
    value = clean_name(value).strip().casefold()
    if value.upper() in COUNTRY_CODES:
        return value.upper()
    return COUNTRY_ALIASES.get(value)


def tournament_countries(pages):
    evidence = {}
    pattern = r"(?:ParticipantSection|ParticipantTable|TeamCard|SoloOpp|SoloOpponent|DuoOpponent|TrioOpponent|Opponent|prize pool slot)"
    for title, raw in pages.items():
        match = re.fullmatch(r"Rusaoc Cup/(\d+)(?:/Qualifier/\d+)?", title)
        if not match:
            continue
        cup = int(match.group(1))
        source = "https://liquipedia.net/ageofempires/" + quote(title.replace(" ", "_"), safe="/")
        for template in nested(strip_comments(raw), pattern):
            kind, fields, positional = parse_tpl(template)
            pairs = [(value, fields.get(field + "flag", "")) for field, value in fields.items() if re.fullmatch(r"p\d+", field)]
            if kind.casefold() in ("soloopp", "soloopponent", "opponent"):
                pairs.append((fields.get("name") or fields.get("player") or (positional[0] if positional else ""), fields.get("flag", "")))
            elif kind.casefold() == "prize pool slot" and positional:
                pairs.append((positional[0], fields.get("flag1", "")))
            for name, flag in pairs:
                player, code = key(canon(name)), country_code(flag)
                if not player or not code:
                    continue
                previous = evidence.get(player)
                if previous is None or cup > previous[0]:
                    evidence[player] = (cup, {code}, source)
                elif cup == previous[0]:
                    previous[1].add(code)
    # A current profile can resolve conflicts; otherwise omit ambiguous flags.
    return {name: {"code": next(iter(codes)), "source": source}
            for name, (_, codes, source) in evidence.items() if len(codes) == 1}


def fetch_player_profiles(names):
    titles = sorted({canon(name) for name in names if canon(name)}, key=str.casefold)
    responses = []
    for offset in range(0, len(titles), 25):
        batch = titles[offset:offset + 25]
        response = api_query(action="query", prop="revisions", rvprop="content", rvslots="main", redirects=1, titles="|".join(batch))
        query = response.get("query", {})
        if not isinstance(query.get("pages"), list):
            raise RuntimeError("Incomplete player profile response")
        redirects = {key(item["from"]): key(item["to"]) for field in ("normalized", "redirects") for item in query.get(field, [])}
        returned = {key(page["title"]) for page in query["pages"]}
        for title in batch:
            target, seen = key(title), set()
            while target in redirects and target not in seen:
                seen.add(target)
                target = redirects[target]
            if target not in returned:
                raise RuntimeError(f"Missing profile response: {title}")
        for page in query["pages"]:
            if "missing" not in page and "invalid" not in page and "revisions" not in page:
                raise RuntimeError(f"Missing profile content: {page['title']}")
        responses.append(response)
        print(f"Checked {min(offset + 25, len(titles))}/{len(titles)} player profiles", flush=True)
        if offset + 25 < len(titles):
            time.sleep(2)
    return responses


def profile_countries(responses):
    countries, redirects = {}, {}
    for response in responses:
        query = response["query"]
        for field in ("normalized", "redirects"):
            redirects.update((key(item["from"]), key(item["to"])) for item in query.get(field, []))
        for page in query["pages"]:
            if "revisions" not in page:
                continue
            raw = page["revisions"][0]["slots"]["main"]["content"]
            infobox = next(nested(strip_comments(raw), "Infobox player"), None)
            if not infobox:
                continue
            _, fields, _ = parse_tpl(infobox)
            code = country_code(fields.get("country", ""))
            if code:
                countries[key(page["title"])] = {"code": code, "source": "https://liquipedia.net/ageofempires/" + quote(page["title"].replace(" ", "_"), safe="/")}
    for alias in redirects:
        target, seen = alias, set()
        while target in redirects and target not in seen:
            seen.add(target)
            target = redirects[target]
        if target in countries:
            countries[alias] = countries[target]
    return {key(canon(name)): country for name, country in countries.items()}


def make_countries(names, responses):
    countries = tournament_countries(PAGES)
    countries.update(profile_countries(responses))
    # Keep original spreadsheet spellings, but only merge already established aliases.
    for name in names:
        country = countries.get(key(canon(name)))
        if country:
            countries[key(name)] = country
    return {"updated": datetime.datetime.now(datetime.timezone.utc).isoformat(), "players": countries}


def read_seed(path):
    match = re.search(r'<script id="seed" type="application/json">(.*?)</script>', path.read_text(encoding="utf-8"), re.S)
    if not match:
        raise RuntimeError(f"Seed element not found: {path}")
    return json.loads(match.group(1))


def api_query(**params):
    params.update(format="json", formatversion=2, maxlag=5)
    url = API + "?" + urlencode(params)
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"})
    for attempt in range(4):
        try:
            with urlopen(request, timeout=75) as response:
                payload = response.read()
                if response.headers.get("Content-Encoding") == "gzip":
                    payload = gzip.decompress(payload)
            result = json.loads(payload)
            if "error" in result:
                raise RuntimeError(str(result["error"]))
            return result
        except (HTTPError, URLError, TimeoutError, RuntimeError) as error:
            if attempt == 3:
                raise RuntimeError(f"Liquipedia API: {error}") from error
            wait = 5 * (attempt + 1)
            if isinstance(error, HTTPError):
                wait = max(wait, int(error.headers.get("Retry-After", 0) or 0))
            print(f"API unavailable ({error}); retrying in {wait}s", file=sys.stderr)
            time.sleep(wait)


def fetch_pages():
    titles = []
    apcontinue = None
    while True:
        kwargs = dict(action="query", list="allpages", apprefix="Rusaoc Cup/", apnamespace=0, aplimit=500)
        if apcontinue:
            kwargs["apcontinue"] = apcontinue
        response = api_query(**kwargs)
        titles.extend(p["title"] for p in response["query"]["allpages"])
        apcontinue = response.get("continue", {}).get("apcontinue")
        if not apcontinue:
            break
    main_titles = [t for t in titles if re.fullmatch(r"Rusaoc Cup/\d+", t)]
    qualifier_titles = [t for t in titles if re.fullmatch(r"Rusaoc Cup/\d+/Qualifier/\d+", t)]
    requested = sorted(main_titles, key=lambda t: int(t.split("/")[-1])) + qualifier_titles
    if not main_titles:
        raise RuntimeError("No tournament pages returned by Liquipedia")
    pages = {}
    for offset in range(0, len(requested), 25):
        batch = requested[offset:offset + 25]
        result = api_query(action="query", prop="revisions", rvprop="content", rvslots="main", titles="|".join(batch))
        for page in result["query"]["pages"]:
            if "missing" in page or "revisions" not in page:
                raise RuntimeError(f"Missing page: {page.get('title')}")
            pages[page["title"]] = page["revisions"][0]["slots"]["main"]["content"]
        print(f"Fetched {len(pages)}/{len(requested)} pages", flush=True)
        if offset + 25 < len(requested):
            time.sleep(2)
    if set(requested) != set(pages):
        raise RuntimeError("Incomplete Liquipedia response")
    return pages


def build():
    main_titles = sorted((t for t in PAGES if re.fullmatch(r"Rusaoc Cup/\d+", t)), key=lambda t: int(t.split("/")[-1]))
    parsed = [parse_page(t, PAGES[t]) for t in main_titles]
    today = datetime.date.today()
    past = [p["cup"] for p in parsed if p["date"] and p["date"] <= today.isoformat()]
    if not past:
        raise RuntimeError("No dated past events found")
    latest = max(past)
    pages = [p for p in parsed if p["cup"] <= latest]
    if [p["cup"] for p in pages] != list(range(1, latest + 1)):
        raise RuntimeError("The numbered tournament archive has missing pages")
    by_cup = {p["cup"]: p for p in pages}
    for title in sorted(PAGES):
        qualifier = re.fullmatch(r"Rusaoc Cup/(\d+)/Qualifier/\d+", title)
        if not qualifier or int(qualifier.group(1)) not in by_cup:
            continue
        main = by_cup[int(qualifier.group(1))]
        part = parse_page(title, PAGES[title])
        main["participants"] = sorted(set(main["participants"]) | set(part["participants"]), key=str.casefold)
        for name, counts in part["stats"].items():
            prior = main["stats"].setdefault(name, [0, 0, 0])
            for i in range(3):
                prior[i] += counts[i]
        for metric, value in part["quality"].items():
            main["quality"][metric] += value
        main["declared"] = None
        main["qualifiersIncluded"] = main.get("qualifiersIncluded", 0) + 1
    return pages


def make_seed(pages):
    players, events = {}, []
    for idx, page in enumerate(pages):
        mode = page["mode"]
        size = 2 if mode == "2v2" else 3 if mode == "3v3" else 1
        declared = page["declared"] * size if page["declared"] is not None else None
        observed = {key(x) for x in page["participants"] if key(x)}
        score_records = sum(1 for values in page["stats"].values() if values[2] > 0)
        original = PAGES[f'Rusaoc Cup/{page["cup"]}']
        notice = bool(re.search(r"\{\{Want to Help\|issue=[^|}]*?(?:missing|not finished)", original, re.I))
        incomplete = (declared is not None and len(observed) < declared) or page["quality"]["unresolved"] > 0 or page["quality"]["unknownScores"] > 0 or notice
        status = "empty" if not observed else "limited" if incomplete or score_records == 0 else "recorded"
        events.append({"cup": page["cup"], "mode": mode, "date": page["date"], "declared": declared, "observed": len(observed), "matches": page["quality"]["matches"], "scoredMatches": page["quality"]["mapMatches"] + page["quality"]["scoreMatches"], "unresolved": page["quality"]["unresolved"], "unknownScores": page["quality"]["unknownScores"], "status": status, "qualifiersIncluded": page.get("qualifiersIncluded", 0)})
        grouped = {}
        for name in page["participants"]:
            k = key(name)
            if k:
                grouped.setdefault(k, {"names": collections.Counter(), "w": 0, "l": 0, "g": 0, "champ": False})["names"][name] += 1
        for name, score in page["stats"].items():
            k = key(name)
            if not k:
                continue
            row = grouped.setdefault(k, {"names": collections.Counter(), "w": 0, "l": 0, "g": 0, "champ": False})
            row["names"][name] += 1
            row["w"] += score[0]
            row["l"] += score[1]
            row["g"] += score[2]
        for name in page["champions"]:
            k = key(name)
            if not k:
                continue
            row = grouped.setdefault(k, {"names": collections.Counter(), "w": 0, "l": 0, "g": 0, "champ": False})
            row["names"][name] += 1
            row["champ"] = True
        for k, row in grouped.items():
            player = players.setdefault(k, {"names": collections.Counter(), "results": []})
            player["names"].update(row["names"])
            player["results"].append([idx, row["w"], row["l"], row["g"], int(row["champ"])])
    final = []
    for player in players.values():
        name = sorted(player["names"].items(), key=lambda item: (-item[1], -len(item[0]), item[0].casefold()))[0][0]
        final.append({"name": name, "results": player["results"]})
    final.sort(key=lambda player: player["name"].casefold())
    return {"updated": datetime.datetime.now(datetime.timezone.utc).isoformat(), "events": events, "players": final}


def replace_json(page, element, value):
    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace("</", r"<\/")
    pattern = r'(<script id="' + element + r'" type="application/json">)(.*?)(</script>)'
    updated, count = re.subn(pattern, lambda match: match.group(1) + payload + match.group(3), page, count=1, flags=re.S)
    if count != 1:
        raise RuntimeError(f"{element} element not found in HTML")
    return updated


def write_html(seed, countries):
    prepared = {}
    for path in (OUTPUT, SHEET_OUTPUT):
        if not path.exists():
            continue
        page = path.read_text(encoding="utf-8")
        if path == OUTPUT:
            page = replace_json(page, "seed", seed)
        prepared[path] = replace_json(page, "countries", countries)
    # Validate both documents before replacing either file.
    for path, page in prepared.items():
        path.with_suffix(".html.tmp").write_text(page, encoding="utf-8")
    for path in prepared:
        path.with_suffix(".html.tmp").replace(path)


def main():
    global PAGES
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--flags-only", action="store_true", help="Update country flags in both HTML files without changing tournament results")
    args = parser.parse_args()
    if not OUTPUT.exists():
        raise RuntimeError(f"HTML file not found: {OUTPUT}")
    PAGES = fetch_pages()
    seed = read_seed(OUTPUT) if args.flags_only else make_seed(build())
    if not seed["players"]:
        raise RuntimeError("No players parsed; keeping existing HTML")
    names = {player["name"] for player in seed["players"]}
    if SHEET_OUTPUT.exists():
        names.update(player["name"] for player in read_seed(SHEET_OUTPUT)["players"])
    countries = make_countries(names, fetch_player_profiles(names))
    write_html(seed, countries)
    games = sum(row[3] for player in seed["players"] for row in player["results"])
    print(f"Updated {OUTPUT.name}: {len(seed['events'])} cups, {len(seed['players'])} players, {games} player-map records")
    for path in (OUTPUT, SHEET_OUTPUT):
        if path.exists():
            players = read_seed(path)["players"]
            flagged = sum(key(player["name"]) in countries["players"] for player in players)
            print(f"Country flags in {path.name}: {flagged}/{len(players)} players")


if __name__ == "__main__":
    main()
