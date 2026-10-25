#!/usr/bin/env python3
"""Dependency-free real-time Fact or Fiction game server."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
import json, secrets, string, threading, time

ROOT = Path(__file__).parent
LOCK = threading.RLock()
ROOMS = {}

QUESTIONS = [
    ("What is the national animal of Scotland?", "The unicorn"),
    ("Which planet rains diamonds, according to scientists?", "Neptune"),
    ("What is the only mammal capable of true flight?", "The bat"),
    ("What was the first feature-length animated film?", "Snow White and the Seven Dwarfs"),
    ("How many hearts does an octopus have?", "Three"),
    ("Which fruit has its seeds on the outside?", "The strawberry"),
    ("What is the smallest bone in the human body?", "The stapes in the ear"),
    ("Which animal's fingerprints are remarkably similar to humans'?", "The koala"),
    ("What is the dot over a lowercase i or j called?", "A tittle"),
    ("Which country has more lakes than the rest of the world combined?", "Canada"),
    ("What is the collective noun for a group of flamingos?", "A flamboyance"),
    ("Which was invented first: the lighter or the match?", "The lighter"),
    ("What color is an airplane's black box?", "Bright orange"),
    ("Which bird can fly backwards?", "The hummingbird"),
    ("What is the only letter that does not appear in any U.S. state name?", "Q"),
    ("What is the world's largest desert?", "Antarctica"),
    ("Which animal can hold its breath underwater for up to two hours?", "The sloth"),
    ("What is the name for the smell of rain on dry ground?", "Petrichor"),
    ("Which sea creature has blue blood?", "The horseshoe crab"),
    ("What is a group of crows called?", "A murder"),
    ("Which planet has the shortest day?", "Jupiter"),
    ("What is the only food that never spoils?", "Honey"),
    ("How many noses does a slug have?", "Four"),
    ("Which animal's tongue can be longer than its body?", "The chameleon"),
]

def new_code():
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    while True:
        code = "".join(secrets.choice(alphabet) for _ in range(4))
        if code not in ROOMS: return code

def public(room, viewer=None):
    players = [{"id": p["id"], "name": p["name"], "score": p["score"],
                "connected": p["connected"]} for p in room["players"]]
    current = room["round"]
    data = {"code": room["code"], "host": room["host"], "players": players,
            "status": room["status"], "roundNumber": room["roundNumber"],
            "totalRounds": len(room["players"]), "phase": room["phase"],
            "scores": [{"id": p["id"], "name": p["name"], "score": p["score"]}
                       for p in sorted(room["players"], key=lambda x: (-x["score"], x["name"].lower()))],
            "winnerIds": room.get("winnerIds", []), "lastResult": room.get("lastResult")}
    if current:
        source = next((p for p in room["players"] if p["id"] == current["sourceId"]), None)
        data["sourceId"] = current["sourceId"]
        data["sourceName"] = source["name"] if source else "Source"
        data["question"] = current["question"]
        data["trueAnswer"] = current["answer"] if viewer == current["sourceId"] or room["phase"] in ("reveal", "finished") else None
        data["decoysSubmitted"] = len(current["decoys"])
        data["votesSubmitted"] = len(current["votes"])
        if room["phase"] in ("voting", "reveal", "finished"):
            data["choices"] = [{"id": c["id"], "text": c["text"]} for c in current["choices"]]
        data["myDecoy"] = current["decoys"].get(viewer) if viewer else None
        data["hasVoted"] = viewer in current["votes"] if viewer else False
        data["myVote"] = current["votes"].get(viewer) if viewer and room["phase"] in ("reveal", "finished") else None
        data["mySubmitted"] = viewer in current["decoys"] if viewer else False
    return data

def snapshot_all(room):
    for p in room["players"]:
        p["events"].append({"type":"state", "data":public(room, p["id"])})
        if len(p["events"]) > 30: p["events"][:] = p["events"][-20:]
        p["event"].set()

def start_round(room):
    idx = room["roundNumber"]
    q, a = QUESTIONS[(idx * 7 + secrets.randbelow(len(QUESTIONS))) % len(QUESTIONS)]
    source = room["players"][idx]
    room["round"] = {"sourceId": source["id"], "question": q, "answer": a,
                     "decoys": {}, "votes": {}, "choices": []}
    room["phase"] = "decoys"

def resolve(room):
    rnd = room["round"]
    correct_id = "truth"
    choices = rnd["choices"]
    fooled = 0
    by_player = {p["id"]:p for p in room["players"]}
    for voter_id, choice_id in rnd["votes"].items():
        if choice_id == correct_id:
            by_player[voter_id]["score"] += 2
        else:
            if voter_id != rnd["sourceId"]:
                fooled += 1
            author = choice_id[2:] if choice_id.startswith("d:") else None
            if author in by_player: by_player[author]["score"] += 1
    source = by_player[rnd["sourceId"]]
    source["score"] += fooled
    room["lastResult"] = {"answer":rnd["answer"], "fooled":fooled,
                           "votes":{k:v for k,v in rnd["votes"].items()},
                           "choices":[{"id":c["id"],"text":c["text"],"authorId":c["authorId"],
                                       "authorName":by_player[c["authorId"]]["name"] if c["authorId"] else None,
                                       "voteCount":sum(1 for v in rnd["votes"].values() if v==c["id"])} for c in choices]}
    room["phase"] = "reveal"

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *_): pass
    def send_json(self, status, data):
        raw=json.dumps(data).encode(); self.send_response(status); self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",str(len(raw))); self.send_header("Access-Control-Allow-Origin","*"); self.end_headers(); self.wfile.write(raw)
    def body(self):
        return json.loads(self.rfile.read(int(self.headers.get("Content-Length",0))) or b"{}")
    def do_OPTIONS(self): self.send_response(204); self.send_header("Access-Control-Allow-Origin","*"); self.send_header("Access-Control-Allow-Headers","Content-Type"); self.send_header("Access-Control-Allow-Methods","GET,POST,OPTIONS"); self.end_headers()
    def do_GET(self):
        path=urlparse(self.path).path
        if path=="/events":
            q=urlparse(self.path).query
            args=dict(x.split("=",1) for x in q.split("&") if "=" in x)
            with LOCK:
                room=ROOMS.get(args.get("code","")); player=next((p for p in room["players"] if p["id"]==args.get("pid")),None) if room else None
                if not player: return self.send_json(404,{"error":"Player not found"})
                player["connected"]=True; player["event"].set(); player["events"].append({"type":"state","data":public(room,player["id"])})
            self.send_response(200); self.send_header("Content-Type","text/event-stream"); self.send_header("Cache-Control","no-cache"); self.send_header("Connection","keep-alive"); self.send_header("Access-Control-Allow-Origin","*"); self.end_headers()
            self.wfile.write(b": connected\n\n"); self.wfile.flush()
            last=0
            try:
                while True:
                    with LOCK:
                        if player["events"]:
                            events=player["events"][:]; player["events"].clear()
                        else: events=[]
                    if events:
                        for e in events:
                            payload=json.dumps(e,separators=(",",":"))
                            self.wfile.write(("data: "+payload+"\n\n").encode())
                        self.wfile.flush(); last=time.time()
                    elif time.time()-last > 15:
                        self.wfile.write(b": ping\n\n"); self.wfile.flush(); last=time.time()
                    player["event"].wait(1); player["event"].clear()
            except (BrokenPipeError, ConnectionResetError, OSError):
                with LOCK: player["connected"]=False
            return
        if path=="/api/health": return self.send_json(200,{"ok":True})
        rel="index.html" if path=="/" else path.lstrip("/")
        target=(ROOT/"public"/rel).resolve()
        if not str(target).startswith(str((ROOT/"public").resolve())) or not target.is_file(): return self.send_error(404)
        raw=target.read_bytes(); typ="text/html" if target.suffix==".html" else "text/css" if target.suffix==".css" else "text/javascript" if target.suffix==".js" else "application/octet-stream"
        self.send_response(200); self.send_header("Content-Type",typ); self.send_header("Content-Length",str(len(raw))); self.end_headers(); self.wfile.write(raw)
    def do_POST(self):
        try:
            data=self.body(); path=urlparse(self.path).path
            with LOCK:
                if path=="/api/create":
                    name=str(data.get("name","")).strip()[:20]
                    if not name: return self.send_json(400,{"error":"Choose a name first."})
                    code=new_code(); pid=secrets.token_urlsafe(9)
                    p={"id":pid,"name":name,"score":0,"connected":True,"events":[],"event":threading.Event()}
                    ROOMS[code]={"code":code,"host":pid,"players":[p],"status":"lobby","phase":"lobby","roundNumber":0,"round":None,"lastResult":None}
                    return self.send_json(200,{"code":code,"pid":pid,"state":public(ROOMS[code],pid)})
                if path=="/api/join":
                    code=str(data.get("code","")).strip().upper(); name=str(data.get("name","")).strip()[:20]; room=ROOMS.get(code)
                    if not name or not room: return self.send_json(404,{"error":"Room code not found. Check it and try again."})
                    if room["status"]!="lobby": return self.send_json(400,{"error":"That game has already started."})
                    if len(room["players"])>=8: return self.send_json(400,{"error":"This room is full (8 players max)."})
                    pid=secrets.token_urlsafe(9); p={"id":pid,"name":name,"score":0,"connected":True,"events":[],"event":threading.Event()}; room["players"].append(p); snapshot_all(room)
                    return self.send_json(200,{"code":code,"pid":pid,"state":public(room,pid)})
                code=str(data.get("code","")).upper(); pid=data.get("pid"); room=ROOMS.get(code)
                if not room: return self.send_json(404,{"error":"Room not found."})
                me=next((p for p in room["players"] if p["id"]==pid),None)
                if not me: return self.send_json(403,{"error":"Player session not found."})
                if path=="/api/leave":
                    if room["status"]!="lobby": return self.send_json(400,{"error":"This game is already underway."})
                    room["players"].remove(me)
                    me["connected"]=False
                    if not room["players"]:
                        del ROOMS[code]
                    else:
                        if room["host"]==pid: room["host"]=room["players"][0]["id"]
                        snapshot_all(room)
                    return self.send_json(200,{"ok":True})
                elif path=="/api/start":
                    if pid!=room["host"]: return self.send_json(403,{"error":"Only the host can start."})
                    if len(room["players"])<2: return self.send_json(400,{"error":"Invite at least one more player to start."})
                    if room["status"]!="lobby": return self.send_json(400,{"error":"Game already started."})
                    room["status"]="playing"; room["roundNumber"]=0; start_round(room); snapshot_all(room)
                elif path=="/api/decoy":
                    if room["phase"]!="decoys" or pid==room["round"]["sourceId"]: return self.send_json(400,{"error":"You can't submit a decoy right now."})
                    answer=" ".join(str(data.get("answer","")).split())[:100]
                    if len(answer)<2: return self.send_json(400,{"error":"Write a decoy with at least 2 characters."})
                    if pid in room["round"]["decoys"]: return self.send_json(400,{"error":"Your decoy is already locked in."})
                    room["round"]["decoys"][pid]=answer
                    if len(room["round"]["decoys"])==len(room["players"])-1:
                        room["phase"]="voting"
                        room["round"]["choices"]=[{"id":"truth", "text":room["round"]["answer"], "authorId":None}]
                        room["round"]["choices"].extend({"id":"d:"+player_id, "text":value, "authorId":player_id} for player_id,value in room["round"]["decoys"].items())
                        secrets.SystemRandom().shuffle(room["round"]["choices"])
                    snapshot_all(room)
                elif path=="/api/vote":
                    if room["phase"]!="voting": return self.send_json(400,{"error":"Voting is not open."})
                    choice=str(data.get("choice","")); valid=["truth"]+["d:"+x for x in room["round"]["decoys"]]
                    if choice not in valid: return self.send_json(400,{"error":"Choose an answer from the list."})
                    if pid in room["round"]["votes"]: return self.send_json(400,{"error":"Your vote is already locked in."})
                    room["round"]["votes"][pid]=choice
                    if len(room["round"]["votes"])==len(room["players"]): resolve(room)
                    snapshot_all(room)
                elif path=="/api/next":
                    if pid!=room["host"]: return self.send_json(403,{"error":"Only the host can continue."})
                    if room["phase"]!="reveal": return self.send_json(400,{"error":"This round is still in progress."})
                    room["roundNumber"]+=1; room["lastResult"]=None
                    if room["roundNumber"]>=len(room["players"]):
                        room["phase"]="finished"; room["status"]="finished"; top=max(p["score"] for p in room["players"]); room["winnerIds"]=[p["id"] for p in room["players"] if p["score"]==top]
                    else: start_round(room)
                    snapshot_all(room)
                elif path=="/api/state": return self.send_json(200,{"state":public(room,pid)})
                else: return self.send_json(404,{"error":"Unknown action."})
                return self.send_json(200,{"state":public(room,pid)})
        except Exception as e:
            return self.send_json(500,{"error":"The game hit a server error."})

if __name__=="__main__":
    print("Fact or Fiction running at http://localhost:8000")
    ThreadingHTTPServer(("0.0.0.0",8000),Handler).serve_forever()
