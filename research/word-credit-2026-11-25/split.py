import sqlite3, sys, re, unicodedata
S=sys.argv[1]
c=sqlite3.connect(f"file:{S}/alif_work.db?mode=ro",uri=True)
def nk(s):
    s="".join(ch for ch in unicodedata.normalize("NFC",s or "") if not ("ً"<=ch<="ْ" or ch in "ٰـ"))
    return re.sub("[أإآٱ]","ا",s).replace("ى","ي")
have={}
for lid,ar,bare,pos,gl,canon,cat in c.execute("select lemma_id,lemma_ar,lemma_ar_bare,pos,gloss_en,canonical_lemma_id,word_category from lemmas"):
    for k in {nk(bare), nk(ar), re.sub("^ال","",nk(bare))}: have.setdefault(k,[]).append((lid,ar,pos,(gl or "")[:24],canon))
miss=open(f"{S}/cand_missing.tsv","w"); ex=open(f"{S}/cand_exists.tsv","w")
nm=ne=tm=te=0
for line in open(f"{S}/cand_new.tsv"):
    n,lex,pos,surf,cred=line.rstrip("\n").split("\t")
    h=have.get(nk(lex))
    if h:
        ne+=1; te+=int(n); ex.write(f"{n}\t{lex}\t{pos}\t{cred}\t{h[:2]}\n")
    else:
        nm+=1; tm+=int(n); miss.write(f"{n}\t{lex}\t{pos}\t{surf}\t{cred}\n")
print("missing groups",nm,"tokens",tm,"| exists groups",ne,"tokens",te)
