#!/usr/bin/env python3
"""Publicador Fast: publica na hora certa o que a fila de marketing já tem pronto.

O que faz, em português simples:
  - lê a fila de dezembro (fila.json) e separa o que é "AUTO" (sobe sozinho);
  - na hora marcada, publica no Facebook e no Instagram da loja pela API da Meta;
  - guarda o que já foi publicado em publicador_estado.json (não repete nada).

Segurança:
  - sem --executar ele só MOSTRA o que faria (ensaio). Nada vai ao ar.
  - nunca publica peça que não seja AUTO, nem peça atrasada mais que 30 minutos.
  - chaves ficam em .cofre/chaves.env (META_PUBLISH_TOKEN). Nunca no repositório.

Uso:
  python scripts/marketing/publicador.py verificar          # confere o token e as permissões
  python scripts/marketing/publicador.py preparar           # converte as artes para JPEG e separa o que vai ser hospedado
  python scripts/marketing/publicador.py ensaio             # lista o que seria publicado nos próximos 60 min
  python scripts/marketing/publicador.py executar           # publica de verdade o que estiver na janela
Opções: --janela MIN (padrão 15)  --agora AAAA-MM-DDTHH:MM (para testes)  --fila CAMINHO
"""
import argparse, datetime, hashlib, json, os, re, shutil, sys, time
import urllib.parse, urllib.request, urllib.error

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MARK = 'C:/Users/rods_/OneDrive/Franquia - FAST/Claude/Marketing/'
FILA = MARK + 'FILA_DEZEMBRO_01-31/fila.json'
PASTA = MARK + 'FILA_DEZEMBRO_01-31/'
HOSPEDAGEM_LOCAL = MARK + 'FILA_DEZEMBRO_01-31/_PUBLICO/'   # pasta que vira o endereço público
ESTADO = MARK + 'FILA_DEZEMBRO_01-31/publicador_estado.json'
GRAPH = 'https://graph.facebook.com/v20.0'
MAX_ATRASO_MIN = 30
NUVEM = os.environ.get('FAST_NUVEM') == '1'   # roda no GitHub Actions: fila, artes e estado ficam no repositório de hospedagem
if NUVEM:
    ESTADO = 'estado/estado.json'
    FILA = 'fila/fila_nuvem.json'
CONTAS = {  # data/config.json > midia_ids
    'ESCOVA': dict(page='1177609485429072', ig='17841439811993335'),
    'SPA': dict(page='871300609405142', ig='17841441683971969'),
}
DN = ['seg', 'ter', 'qua', 'qui', 'sex', 'sáb', 'dom']


def carrega_env():
    p = os.path.join(RAIZ, '.cofre', 'chaves.env')
    if os.path.exists(p):
        for l in open(p, encoding='utf-8'):
            l = l.strip()
            if l and not l.startswith('#') and '=' in l:
                k, v = l.split('=', 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"'))


def http(url, dados=None, arquivos=None, metodo=None):
    """Chamada simples à API. dados = dict; arquivos = {campo: caminho}."""
    cab = {}
    corpo = None
    if arquivos:
        lim = '----fast' + hashlib.md5(str(time.time()).encode()).hexdigest()
        partes = []
        for k, v in (dados or {}).items():
            partes.append(('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n' % (lim, k, v)).encode())
        for k, caminho in arquivos.items():
            partes.append(('--%s\r\nContent-Disposition: form-data; name="%s"; filename="%s"\r\nContent-Type: application/octet-stream\r\n\r\n' % (lim, k, os.path.basename(caminho))).encode())
            partes.append(open(caminho, 'rb').read()); partes.append(b'\r\n')
        partes.append(('--%s--\r\n' % lim).encode())
        corpo = b''.join(partes); cab['Content-Type'] = 'multipart/form-data; boundary=' + lim
    elif dados is not None:
        corpo = urllib.parse.urlencode(dados).encode()
    req = urllib.request.Request(url, data=corpo, headers=cab, method=metodo)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError('Meta recusou: %s' % e.read().decode()[:400])


def token():
    t = os.environ.get('META_PUBLISH_TOKEN') or os.environ.get('META_ACCESS_TOKEN')
    if not t:
        sys.exit('NÃO VEJO a chave: ponha META_PUBLISH_TOKEN em .cofre/chaves.env')
    return t


def token_pagina(loja):
    """Troca o token de usuário pelo token da página (necessário para publicar)."""
    r = http('%s/%s?fields=access_token&access_token=%s' % (GRAPH, CONTAS[loja]['page'], token()))
    return r.get('access_token') or token()


def verificar():
    t = token()
    try:
        r = http('%s/me/permissions?access_token=%s' % (GRAPH, t))
        ok = {p['permission'] for p in r.get('data', []) if p.get('status') == 'granted'}
    except RuntimeError as e:
        print('Não consegui listar permissões:', e); ok = set()
    precisa = ['pages_manage_posts', 'pages_read_engagement', 'instagram_basic', 'instagram_content_publish']
    for p in precisa:
        print(('OK     ' if p in ok else 'FALTA  ') + p)
    for loja, c in CONTAS.items():
        try:
            pg = http('%s/%s?fields=name&access_token=%s' % (GRAPH, c['page'], t)); print(loja, 'página:', pg.get('name'))
        except RuntimeError as e:
            print(loja, 'página: ERRO', e)
    print('Resultado:', 'pronto para publicar' if all(p in ok for p in precisa) else 'faltam permissões: o token atual só serve para ler')


FILA_NOVEMBRO = MARK + 'FILA_NOVEMBRO_27-30/fila.json'  # peças soltas de novembro, com caminhos completos (abs=true)


def carrega_fila_nuvem(caminho):
    itens = []
    for it in json.load(open(caminho, encoding='utf-8')):
        itens.append(dict(id=it['id'], loja=it['loja'], quando=datetime.datetime.fromisoformat(it['quando']), fmt=it['fmt'], nome=it['nome'],
                          status=it['status'], legenda=it.get('legenda') or '', arqs=[os.path.join('.', n) for n in it['arquivos']]))
    return sorted(itens, key=lambda x: x['quando'])


def exportar_nuvem(destino):
    """Grava a fila no formato da nuvem (só peças AUTO com arte). Os arquivos viram os nomes públicos (hash.jpg / hash.mp4)."""
    out = []
    for it in carrega_fila(FILA):
        if it['status'] != 'AUTO' or not it['arqs']:
            continue
        nomes = [nome_publico(a) for a in it['arqs'] if a.lower().endswith(('.png', '.jpg', '.jpeg', '.mp4', '.mov'))]
        if not nomes:
            continue
        out.append(dict(id=it['id'], loja=it['loja'], quando=it['quando'].isoformat(timespec='minutes'), fmt=it['fmt'], nome=it['nome'], status='AUTO', legenda=it['legenda'], arquivos=nomes))
    os.makedirs(os.path.dirname(destino), exist_ok=True)
    json.dump(out, open(destino, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    return len(out)


def carrega_fila(caminho):
    if NUVEM:
        return carrega_fila_nuvem(caminho)
    arquivos = [caminho]
    if caminho == FILA and os.path.exists(FILA_NOVEMBRO):
        arquivos.append(FILA_NOVEMBRO)
    itens = []
    for cam in arquivos:
      f = json.load(open(cam, encoding='utf-8'))
      for loja, its in f.items():
        for i, it in enumerate(its):
            m = re.match(r'(\d+)h(\d*)', it.get('hora') or '')
            if not m or not it.get('arquivos'):
                continue
            mes = it.get('mes', 12)
            quando = datetime.datetime(it.get('ano', 2026), mes, it['dia'], int(m.group(1)), int(m.group(2) or 0))
            d = '%s/2026-12-%02d_%s/' % (loja, it['dia'], DN[quando.weekday()])
            if it.get('abs'):
                arqs = list(it['arquivos'])
            else:
                arqs = [PASTA + d + it['pref'] + '_%d' % (k + 1) + os.path.splitext(a)[1] for k, a in enumerate(it['arquivos'])]
            itens.append(dict(id='%s|%s|%s|%s' % (loja, quando.strftime('%Y%m%d%H%M'), it['fmt'], it['pref']), loja=loja, quando=quando,
                              fmt=it['fmt'], nome=it['nome'], status=it['status'], legenda=it.get('legenda') or '', arqs=arqs))
    return sorted(itens, key=lambda x: x['quando'])


def estado():
    return json.load(open(ESTADO, encoding='utf-8')) if os.path.exists(ESTADO) else {}


def grava_estado(e):
    json.dump(e, open(ESTADO, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)


def url_publica(arq):
    """A Meta lê o Instagram por endereço público. As artes vão para HOSPEDAGEM_LOCAL em JPEG (preparar)."""
    base = os.environ.get('PUBLICO_BASE_URL')
    if not base:
        sys.exit('NÃO VEJO o endereço público: defina PUBLICO_BASE_URL (depois que você aprovar a hospedagem)')
    return base.rstrip('/') + '/' + nome_publico(arq)


def nome_publico(arq):
    if NUVEM:
        return os.path.basename(arq)
    h = hashlib.sha1(arq.encode()).hexdigest()[:16]
    return h + ('.mp4' if arq.lower().endswith(('.mp4', '.mov')) else '.jpg')


def preparar(itens):
    from PIL import Image
    os.makedirs(HOSPEDAGEM_LOCAL, exist_ok=True)
    n = 0
    for it in itens:
        for a in it['arqs']:
            if a.lower().endswith('.mp4'):  # vídeo já vem pronto (H.264 1080x1920, áudio original): só copia
                dst = HOSPEDAGEM_LOCAL + nome_publico(a)
                if not os.path.exists(dst) and os.path.exists(a):
                    shutil.copy2(a, dst); n += 1
                continue
            if not a.lower().endswith(('.png', '.jpg', '.jpeg')):
                continue
            dst = HOSPEDAGEM_LOCAL + nome_publico(a)
            if os.path.exists(dst):
                continue
            im = Image.open(a).convert('RGB'); im.save(dst, 'JPEG', quality=92, optimize=True); n += 1
            if os.path.getsize(dst) > 8 * 1024 * 1024:
                im.save(dst, 'JPEG', quality=80, optimize=True)
    print('JPEGs preparados:', n, '->', HOSPEDAGEM_LOCAL)
    print('Próximo passo (precisa do seu OK): publicar essa pasta num endereço público e definir PUBLICO_BASE_URL.')


def pub_facebook(it, quando_utc_epoch, agendar):
    c = CONTAS[it['loja']]; tk = token_pagina(it['loja']); ids = []
    if it['fmt'] == 'Reel':  # Reel do Facebook: abrir, mandar o arquivo pelo endereço público, finalizar
        ini = http('%s/%s/video_reels' % (GRAPH, c['page']), dict(access_token=tk, upload_phase='start'))
        req = urllib.request.Request(ini['upload_url'], data=b'', method='POST', headers={'Authorization': 'OAuth ' + tk, 'file_url': url_publica(it['arqs'][0])})
        try:
            urllib.request.urlopen(req, timeout=300).read()
        except urllib.error.HTTPError as e:
            raise RuntimeError('envio do reel recusado: %s' % e.read().decode()[:300])
        fim = http('%s/%s/video_reels' % (GRAPH, c['page']), dict(access_token=tk, upload_phase='finish', video_id=ini['video_id'], video_state='PUBLISHED', description=it['legenda']))
        return [ini['video_id']]
    if it['fmt'] == 'Feed' and len(it['arqs']) > 1:  # carrossel de fotos no Facebook = post com várias fotos
        fotos = [http('%s/%s/photos' % (GRAPH, c['page']), dict(access_token=tk, published='false'), {'source': a})['id'] for a in it['arqs']]
        d = dict(access_token=tk, message=it['legenda'])
        for k, f in enumerate(fotos):
            d['attached_media[%d]' % k] = json.dumps({'media_fbid': f})
        r = http('%s/%s/feed' % (GRAPH, c['page']), d)
        return [r.get('id')]
    for a in it['arqs']:
        d = dict(access_token=tk, published='false')
        if it['fmt'] == 'Feed' and len(it['arqs']) == 1:
            d['caption'] = it['legenda']
            if agendar:
                d['published'] = 'false'; d['scheduled_publish_time'] = str(quando_utc_epoch)
            else:
                d['published'] = 'true'
            r = http('%s/%s/photos' % (GRAPH, c['page']), d, {'source': a}); ids.append(r.get('post_id') or r.get('id'))
        elif it['fmt'] == 'Story':
            r = http('%s/%s/photos' % (GRAPH, c['page']), d, {'source': a})
            s = http('%s/%s/photo_stories' % (GRAPH, c['page']), dict(access_token=tk, photo_id=r['id'])); ids.append(s.get('post_id') or s.get('id'))
        else:
            raise RuntimeError('formato %s no Facebook ainda não automatizado' % it['fmt'])
    return ids


def pub_instagram(it):
    c = CONTAS[it['loja']]; tk = token_pagina(it['loja']); ig = c['ig']
    urls = [url_publica(a) for a in it['arqs']]
    if it['fmt'] == 'Reel':
        r = http('%s/%s/media' % (GRAPH, ig), dict(access_token=tk, video_url=urls[0], media_type='REELS', caption=it['legenda'], share_to_feed='true'))
        for _ in range(40):  # vídeo demora a processar
            st = http('%s/%s?fields=status_code&access_token=%s' % (GRAPH, r['id'], tk)).get('status_code')
            if st == 'FINISHED':
                break
            if st == 'ERROR':
                raise RuntimeError('Instagram recusou o vídeo')
            time.sleep(6)
    elif it['fmt'] == 'Story':
        r = http('%s/%s/media' % (GRAPH, ig), dict(access_token=tk, image_url=urls[0], media_type='STORIES'))
    elif it['fmt'] == 'Feed' and len(urls) == 1:
        r = http('%s/%s/media' % (GRAPH, ig), dict(access_token=tk, image_url=urls[0], caption=it['legenda']))
    elif it['fmt'] in ('Feed', 'Carrossel') and len(urls) > 1:
        filhos = [http('%s/%s/media' % (GRAPH, ig), dict(access_token=tk, image_url=u, is_carousel_item='true'))['id'] for u in urls]
        r = http('%s/%s/media' % (GRAPH, ig), dict(access_token=tk, media_type='CAROUSEL', children=','.join(filhos), caption=it['legenda']))
    else:
        raise RuntimeError('formato %s no Instagram ainda não automatizado' % it['fmt'])
    time.sleep(8)  # a Meta processa o contêiner
    p = http('%s/%s/media_publish' % (GRAPH, ig), dict(access_token=tk, creation_id=r['id']))
    return p.get('id')


def rodar(args):
    if not NUVEM and args.executar and os.path.exists(PASTA + 'NUVEM_ATIVA.txt'):
        print('Nuvem ativa: o publicador do PC está em espera (nada publicado aqui).'); return
    if args.agora:
        agora = datetime.datetime.fromisoformat(args.agora)
    elif NUVEM:
        agora = datetime.datetime.utcnow() - datetime.timedelta(hours=3)
    else:
        agora = datetime.datetime.now()
    itens = carrega_fila(args.fila); est = estado(); feitos = 0
    janela = datetime.timedelta(minutes=args.janela)
    for it in itens:
        if it['id'] in est or it['status'] != 'AUTO':
            continue
        atraso = (agora - it['quando']).total_seconds() / 60
        if it['quando'] > agora + janela:
            continue
        if atraso > MAX_ATRASO_MIN:
            print('ATRASADA (>%d min), não publico: %s %s' % (MAX_ATRASO_MIN, it['quando'].strftime('%d/%m %H:%M'), it['nome'])); continue
        rotulo = '%s %s %s %s' % (it['quando'].strftime('%d/%m %H:%M'), it['loja'], it['fmt'], it['nome'])
        if not args.executar:
            print('ENSAIO ->', rotulo, '(%d arquivo(s))' % len(it['arqs'])); continue
        try:
            ep = int((it['quando'] + datetime.timedelta(hours=3)).replace(tzinfo=datetime.timezone.utc).timestamp())  # BRT=UTC-3
            # Facebook e Instagram saem juntos, na hora marcada (o Instagram não aceita agendar pela API)
            fb = pub_facebook(it, ep, False)
            ig = pub_instagram(it)
            est[it['id']] = dict(fb=fb, ig=ig, feito_em=agora.isoformat(timespec='minutes')); grava_estado(est); feitos += 1
            print('PUBLICADO ->', rotulo)
        except Exception as e:
            print('FALHOU ->', rotulo, '|', e)
    print('Fim: %d publicada(s).' % feitos if args.executar else 'Fim do ensaio.')


def main():
    carrega_env()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('cmd', choices=['verificar', 'preparar', 'ensaio', 'executar', 'exportar'])
    ap.add_argument('--destino', default='fila/fila_nuvem.json'); ap.add_argument('--janela', type=int, default=15); ap.add_argument('--agora'); ap.add_argument('--fila', default=FILA)
    a = ap.parse_args(); a.executar = a.cmd == 'executar'
    if a.cmd == 'verificar': verificar()
    elif a.cmd == 'preparar': preparar(carrega_fila(a.fila))
    elif a.cmd == 'exportar': print('exportadas', exportar_nuvem(a.destino))
    else: rodar(a)


if __name__ == '__main__':
    main()
