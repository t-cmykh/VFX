from PIL import Image, ImageDraw, ImageFont
import os, glob
im = Image.open('ref.jpg').convert('RGB')
# (categorie, nom, (x0,y0,x1,y1))
A = [
("GRUES", "Grue portique géante 'LA CIOTAT 105'", (695,170,1000,340)),
("GRUES", "Grue à tour treillis orange (quai gauche)", (190,265,285,505)),
("GRUES", "Grue à tour treillis (bord gauche)", (0,400,50,625)),
("GRUES", "Grue à flèche treillis blanche (droite)", (1110,222,1285,338)),
("GRUES", "Grue à tour treillis fine (fond droit)", (1035,195,1090,330)),
("GRUES", "Grue sur pylône blanc (cale centrale)", (765,305,860,435)),
("GRUES", "Grue mobile rose (pelle/camion-grue)", (430,335,490,425)),
("BATIMENTS", "Long hangar d'atelier (fond)", (525,228,765,305)),
("BATIMENTS", "Grand bâtiment blanc à gauche (toit plat)", (105,262,305,375)),
("BATIMENTS", "Bâtiments blancs modulaires (blocs)", (305,275,450,350)),
("BATIMENTS", "Hangar à toit voûté + bureaux", (95,488,312,630)),
("BATIMENTS", "Hangar longueur béton/brique (fond droit)", (965,262,1295,325)),
("BATIMENTS", "Petit bâtiment d'accès blanc", (870,290,930,345)),
("BATIMENTS", "Maisons provençales (bord droit)", (1240,285,1320,375)),
("BATIMENTS", "Locaux techniques de quai (cabanes)", (668,668,775,722)),
("QUAIS & GENIE CIVIL", "Plateformes de levage / shiplift noires", (870,430,1120,640)),
("QUAIS & GENIE CIVIL", "Quai béton avec rails de grue", (440,560,720,780)),
("QUAIS & GENIE CIVIL", "Cale sèche (bassin + murs)", (590,380,760,470)),
("QUAIS & GENIE CIVIL", "Passerelle / ponton fixe", (575,455,745,505)),
("QUAIS & GENIE CIVIL", "Quai gauche + pontons", (0,380,300,470)),
("QUAIS & GENIE CIVIL", "Rails & aire de travail (dalle large)", (905,340,1115,470)),
("QUAIS & GENIE CIVIL", "Jetée / môle de pointe", (1130,455,1320,560)),
("PROPS", "Tentes / abris blancs de chantier", (375,445,490,495)),
("PROPS", "Conteneurs & bungalows de chantier", (330,530,390,580)),
("PROPS", "Parking + voitures", (0,600,160,745)),
("PROPS", "Piles de conteneurs / stockage", (90,650,185,705)),
("PROPS", "Bateau de travail / annexe", (380,595,440,645)),
("PROPS", "Voitures & camions sur route de quai", (560,590,760,700)),
("ENVIRONNEMENT", "Rocher 'Bec de l'Aigle' (3 pics)", (585,50,1170,195)),
("ENVIRONNEMENT", "Îlot rocheux (gauche)", (0,135,105,215)),
("ENVIRONNEMENT", "Colline végétalisée (pins) + village", (990,130,1320,265)),
]
W,H = 420,300; PAD=14; COLS=4; LAB=64
os.makedirs('assets', exist_ok=True)
for f in glob.glob('assets/*.jpg'): os.remove(f)
try:
    F=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',17)
    FS=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',13)
    FT=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',34)
except: F=FS=FT=ImageFont.load_default()
tiles=[]
for i,(cat,name,box) in enumerate(A,1):
    c=im.crop(box); s=min(W/c.width,H/c.height)
    c=c.resize((int(c.width*s),int(c.height*s)),Image.LANCZOS)
    t=Image.new('RGB',(W,H+LAB),(24,26,30)); t.paste(c,((W-c.width)//2,(H-c.height)//2))
    d=ImageDraw.Draw(t)
    d.rectangle([0,0,46,32],fill=(230,120,30)); d.text((8,5),f"{i:02d}",font=F,fill='white')
    d.text((10,H+8),name,font=F,fill='white')
    d.text((10,H+36),cat,font=FS,fill=(230,150,70))
    t.save(f"assets/{i:02d}_{cat.split()[0].lower()}.jpg",quality=92); tiles.append((cat,t))
rows=(len(tiles)+COLS-1)//COLS
SW=COLS*W+(COLS+1)*PAD; head=110
SH=head+rows*(H+LAB+PAD)+PAD
S=Image.new('RGB',(SW,SH),(14,15,18)); d=ImageDraw.Draw(S)
d.text((PAD,22),"LA CIOTAT SHIPYARD — MOODBOARD DES ASSETS 3D",font=FT,fill='white')
d.text((PAD,70),f"{len(A)} assets (hors yachts) — recadrages de la photo de référence",font=F,fill=(230,150,70))
for k,(c,t) in enumerate(tiles):
    r,cn=divmod(k,COLS); S.paste(t,(PAD+cn*(W+PAD),head+r*(H+LAB+PAD)))
S.save('moodboard.jpg',quality=90); print(len(A),S.size)
