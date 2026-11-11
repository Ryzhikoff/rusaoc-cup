(() => {
  'use strict';
  const $=id=>document.getElementById(id),page=window.RusaocPage;
  const catalog=JSON.parse($('tournaments').textContent),data=JSON.parse($('seed').textContent);
  const events=catalog.events,byCup=new Map(events.map(e=>[e.cup,e]));
  const number=new Intl.NumberFormat('ru-RU'),rules=new Intl.PluralRules('ru-RU');
  const plural=(n,one,few,many)=>({one,few,many,other:many})[rules.select(n)];
  const esc=value=>String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const money=(value,currency)=>value===null?'—':new Intl.NumberFormat('ru-RU',{style:'currency',currency,currencyDisplay:'narrowSymbol',minimumFractionDigits:currency==='USD'?2:0,maximumFractionDigits:2}).format(value/100);
  const date=value=>value?new Date(value+'T12:00:00Z').toLocaleDateString('ru-RU',{day:'numeric',month:'short',year:'numeric',timeZone:'UTC'}):'Дата не указана';
  const dates=e=>e.date&&e.endDate&&e.date!==e.endDate?`${date(e.date)} — ${date(e.endDate)}`:date(e.date||e.endDate);
  const cupFor=name=>Number(name.match(/^Rusaoc Cup (\d+)/)?.[1]);
  const playerByName=new Map(data.players.map(p=>[p.name,p]));
  let current=null,allMaps=false;
  const recordsFor=e=>data.players.flatMap(p=>{
    const results=p.results.filter(r=>e.sheetIndices.includes(r[0]));
    return results.length?[{name:p.name,results}]:[];
  });
  const roster=new Map(events.map(e=>[e.cup,recordsFor(e)]));
  const countPlayers=e=>e.players??(roster.get(e.cup).length||e.participants.length||null);
  const image=(name,cls='map-thumb')=>catalog.mapImages[name]?`<img class="${cls}" src="${esc(catalog.mapImages[name].path)}" alt="Карта ${esc(name)}" loading="lazy" decoding="async" width="240" height="150">`:'';
  const player=name=>playerByName.has(name)?`<button class="text-button" type="button" data-open-player="${esc(name)}">${page.playerName(name)}</button>`:`<span>${page.playerName(name)}</span>`;

  $('tournaments-view').innerHTML=`
    <div class="catalog-head"><h2>Архив турниров</h2><p>Карты, участники и призовые за всю историю серии.</p></div>
    <section class="summary catalog-metrics" aria-label="Итоги выбранных турниров">
      <div class="metric"><small>Турниров</small><strong id="t-count"></strong><span>каждый выпуск учитывается один раз</span></div>
      <div class="metric"><small>Разных карт</small><strong id="t-map-count"></strong><span id="t-map-coverage"></span></div>
      <div class="metric"><small>Призовые · рубли</small><strong id="t-rub-total"></strong><span id="t-rub-coverage"></span></div>
      <div class="metric"><small>Призовые · доллары</small><strong id="t-usd-total"></strong><span id="t-usd-coverage"></span></div>
    </section>
    <section class="catalog-popular" aria-label="Популярные карты"><div class="catalog-head"><h2>Популярные карты</h2><p>Нажмите на карту, чтобы выбрать турниры с ней.</p></div><div class="popular-maps" id="popular-maps"></div><button id="maps-more" class="maps-more" type="button">Показать все карты</button></section>
    <section class="board" aria-label="Каталог турниров">
      <div class="catalog-toolbar">
        <label>Номер, название или карта<input id="t-search" type="search" placeholder="Найти турнир…"></label>
        <label>Год<select id="t-year"><option value="">Все годы</option></select></label>
        <label>Формат<select id="t-mode"><option value="">Все форматы</option><option>1v1</option><option>2v2</option><option>3v3</option><option>FFA</option></select></label>
        <label>Карта<select id="t-map"><option value="">Все карты</option></select></label>
        <label>Сортировка<select id="t-sort"><option value="newest">Сначала новые</option><option value="oldest">Сначала старые</option><option value="players">Больше игроков</option><option value="rubMinor">Больше призовых · ₽</option><option value="usdMinor">Больше призовых · $</option></select></label>
      </div>
      <div class="catalog-status"><span id="t-result-count"></span><button class="catalog-reset" id="t-reset" type="button">Сбросить фильтры</button></div>
      <div class="table-scroll"><table class="catalog-table"><thead><tr><th>Турнир</th><th>Дата</th><th>Формат</th><th>Карты</th><th>Игроков</th><th>Призовые · ₽</th><th>Призовые · $</th></tr></thead><tbody id="t-rows"></tbody></table></div>
    </section>`;
  const dialog=document.createElement('dialog');
  dialog.id='tournament-detail';dialog.className='tournament-dialog';dialog.setAttribute('aria-labelledby','tournament-heading');
  dialog.innerHTML='<div class="tournament-head"><div><h2 id="tournament-heading"></h2><p id="tournament-subtitle"></p></div><div class="tournament-actions"><button class="about-button" id="tournament-about" type="button" aria-haspopup="dialog" aria-controls="about-data">О данных</button><button class="close" id="tournament-close" type="button" aria-label="Закрыть турнир" autofocus>×</button></div></div><div class="tournament-body" id="tournament-body"></div>';
  document.body.append(dialog);
  const mapNames=[...new Set(events.flatMap(e=>e.maps))].sort((a,b)=>a.localeCompare(b,'ru'));
  $('t-map').innerHTML+=mapNames.map(m=>`<option value="${esc(m)}">${esc(m)}</option>`).join('');
  const years=[...new Set(events.map(e=>(e.date||e.endDate||'').slice(0,4)).filter(Boolean))].sort().reverse();
  $('t-year').innerHTML+=years.map(y=>`<option>${y}</option>`).join('');

  function setView(view,updateUrl=true){
    $('players-view').hidden=view!=='players';$('tournaments-view').hidden=view!=='tournaments';
    $('players-tab').setAttribute('aria-pressed',String(view==='players'));
    $('tournaments-tab').setAttribute('aria-pressed',String(view==='tournaments'));
    if(updateUrl)history.replaceState(null,'','#'+view);
  }
  function filtered(ignoreMap=false){
    const q=$('t-search').value.trim().toLocaleLowerCase(),year=$('t-year').value,mode=$('t-mode').value,map=$('t-map').value;
    return events.filter(e=>(!q||`Rusaoc Cup ${e.cup} ${e.scope} ${e.maps.join(' ')}`.toLocaleLowerCase().includes(q))&&(!year||(e.date||e.endDate||'').startsWith(year))&&(!mode||e.mode===mode)&&(ignoreMap||!map||e.maps.includes(map)));
  }
  function render(){
    const rows=filtered(),sort=$('t-sort').value;
    rows.sort((a,b)=>{
      if(sort==='newest'||sort==='oldest')return (sort==='newest'?-1:1)*(a.cup-b.cup);
      const av=sort==='players'?countPlayers(a):a[sort],bv=sort==='players'?countPlayers(b):b[sort];
      if(av===null)return bv===null?b.cup-a.cup:1;
      if(bv===null)return -1;
      return bv-av||b.cup-a.cup;
    });
    $('t-count').textContent=number.format(rows.length);
    $('t-map-count').textContent=number.format(new Set(rows.flatMap(e=>e.maps)).size);
    $('t-map-coverage').textContent=`карты известны у ${rows.filter(e=>e.maps.length).length} из ${rows.length}`;
    for(const [short,field,currency] of [['rub','rubMinor','RUB'],['usd','usdMinor','USD']]){
      const known=rows.filter(e=>e[field]!==null);
      $('t-'+short+'-total').textContent=known.length?(short==='usd'?'≈ ':'')+money(known.reduce((sum,e)=>sum+e[field],0),currency):'—';
      $('t-'+short+'-coverage').textContent=`известные суммы: ${known.length} из ${rows.length} ${plural(rows.length,'турнира','турниров','турниров')}`;
    }
    $('t-result-count').textContent=`Показано ${rows.length} из ${events.length} ${plural(events.length,'турнира','турниров','турниров')}`;
    $('t-reset').hidden=!($('t-search').value||$('t-year').value||$('t-mode').value||$('t-map').value||sort!=='newest');
    $('t-rows').innerHTML=rows.map(e=>`<tr data-cup="${e.cup}"><td><button type="button" class="text-button" data-cup="${e.cup}">Rusaoc Cup ${e.cup}</button>${e.scope?`<small>${esc(e.scope)}</small>`:''}</td><td>${esc(date(e.date||e.endDate))}${e.date!==e.endDate&&e.endDate?`<small>до ${esc(date(e.endDate))}</small>`:''}</td><td>${esc(e.mode)}</td><td>${e.maps.length?`<div class="event-map-summary">${image(e.maps[0])}<div><button type="button" class="map-filter" data-map="${esc(e.maps[0])}">${esc(e.maps[0])}</button>${e.maps.length>1?`<small title="${esc(e.maps.join(', '))}">ещё ${e.maps.length-1}</small>`:''}</div></div>`:'<span class="muted">Не указаны</span>'}</td><td>${countPlayers(e)??'—'}${e.teams?`<small>${e.teams} ${plural(e.teams,'команда','команды','команд')}</small>`:''}</td><td>${money(e.rubMinor,'RUB')}${e.prizeNote?'<small>есть примечание</small>':''}</td><td>${e.usdMinor===null?'—':'≈ '+money(e.usdMinor,'USD')}</td></tr>`).join('')||'<tr><td colspan="7" class="empty">Турниры не найдены. Попробуйте изменить фильтры.</td></tr>';
    const counts=new Map();
    for(const e of filtered(true))for(const m of new Set(e.maps))counts.set(m,(counts.get(m)||0)+1);
    const popular=[...counts].sort((a,b)=>b[1]-a[1]||a[0].localeCompare(b[0],'ru'));
    $('popular-maps').innerHTML=(allMaps?popular:popular.slice(0,8)).map(([m,n])=>`<button class="popular-map" type="button" data-map="${esc(m)}" aria-pressed="${$('t-map').value===m}">${image(m)}<span>${esc(m)}<small>${n} ${plural(n,'турнир','турнира','турниров')}</small></span></button>`).join('')||'<p class="muted">Нет сведений о картах.</p>';
    $('maps-more').hidden=popular.length<=8;$('maps-more').textContent=allMaps?'Свернуть карты':`Показать все карты (${popular.length})`;
  }
  function chooseMap(name){
    if(dialog.open)dialog.close();
    page.closePlayer();setView('tournaments');$('t-map').value=name;render();$('t-map').focus();
  }
  function stageInfo(e){
    const known=new Set(e.participants),groups=e.sheetIndices.map((idx,n)=>{
      const members=roster.get(e.cup).filter(p=>p.results.some(r=>r[0]===idx));
      const suffix=data.events[idx].name.match(/_(\d+)$/)?.[1];
      return {idx,members,label:suffix?`Квалификация ${suffix}`:`Этап ${n+1}`,overlap:members.filter(p=>known.has(p.name)).length/Math.max(1,members.length)};
    });
    const main=[...groups].sort((a,b)=>b.overlap-a.overlap)[0];
    if(main&&main.overlap>=0.8&&!data.events[main.idx].name.includes('_'))main.label=e.scope||'Основная стадия';
    if(groups.length===1)groups[0].label='Все участники';
    return {groups,main:main&&main.overlap>=0.8?main:null};
  }
  function renderParticipants(){
    const e=current,stage=$('t-stage').value,q=$('t-player-search').value.trim().toLocaleLowerCase();
    let entries=roster.get(e.cup).map(p=>({name:p.name,results:p.results.filter(r=>stage==='all'||r[0]===Number(stage))})).filter(p=>p.results.length);
    if(!e.sheetIndices.length)entries=e.participants.map(name=>({name,results:[]}));
    entries=entries.filter(p=>p.name.toLocaleLowerCase().includes(q)).sort((a,b)=>a.name.localeCompare(b.name,'ru'));
    $('t-participant-count').textContent=`${entries.length} ${plural(entries.length,'игрок','игрока','игроков')}`;
    $('t-player-list').innerHTML=entries.map(p=>{
      const w=p.results.reduce((s,r)=>s+r[1],0),l=p.results.reduce((s,r)=>s+r[2],0),g=p.results.reduce((s,r)=>s+r[3],0);
      return `<div class="tournament-player-row">${player(p.name)}<small>${p.results.length?`${g} ${plural(g,'матч','матча','матчей')}<br>${w} В · ${l} П`:'Результаты не указаны'}</small></div>`;
    }).join('')||'<p class="muted">Игроки не найдены.</p>';
  }
  function open(cup){
    const e=byCup.get(Number(cup));if(!e)return;
    current=e;$('tournament-heading').textContent=`Rusaoc Cup ${e.cup}`;
    $('tournament-subtitle').textContent=[dates(e),e.mode,e.scope].filter(Boolean).join(' · ');
    const stages=stageInfo(e);
    $('tournament-body').innerHTML=`
      <div class="profile-metrics"><div><small>Игроков${e.sheetIndices.length>1?' · основная стадия':''}</small><b>${countPlayers(e)??'—'}</b>${e.teams?`<span>${e.teams} ${plural(e.teams,'команда','команды','команд')}</span>`:''}</div><div><small>Призовой фонд · ₽</small><b>${money(e.rubMinor,'RUB')}</b></div><div><small>Призовой фонд · $</small><b>${e.usdMinor===null?'—':'≈ '+money(e.usdMinor,'USD')}</b></div></div>
      ${e.prizeNote?`<p class="tournament-note">${esc(e.prizeNote)}</p>`:''}${e.scope?`<p class="tournament-note">Карты, основной состав и призовой фонд указаны для ${esc(e.scope)}.</p>`:''}
      <h3>Карты</h3><div class="tournament-maps">${e.maps.map(m=>`<button class="tournament-map" type="button" data-map="${esc(m)}" title="Показать все турниры на ${esc(m)}">${image(m,'')}<span>${esc(m)}</span></button>`).join('')||'<span class="muted">Карта не указана.</span>'}</div>${e.mapNote?`<p class="tournament-note">${esc(e.mapNote)}</p>`:''}
      ${e.qualifiers.length?`<p class="tournament-note">Квалификации: ${e.qualifiers.map(q=>`№${q.number}${q.maps.length?' — '+esc(q.maps.join(', ')):''}`).join('; ')}.</p>`:''}
      <h3>${e.champions.length>1?'Победители':'Победитель'}</h3><div class="champion-list">${e.champions.map(player).join('')||'<span class="muted">Нет сведений</span>'}</div>
      <h3>Распределение призовых</h3>${e.prizes.length?`<div class="table-scroll"><table class="prize-table"><thead><tr><th>Место</th><th>Игроки</th><th>Рубли</th><th>Доллары</th></tr></thead><tbody>${e.prizes.map(p=>`<tr><td>${esc(p.place)}</td><td>${p.players.map(player).join('')||'<span class="muted">Не указаны</span>'}</td><td>${money(p.rubMinor,'RUB')}</td><td>${p.usdMinor===null?'—':'≈ '+money(p.usdMinor,'USD')}</td></tr>`).join('')}</tbody></table></div><p class="tournament-note">Приз указан за одно место${e.teams?' для всей команды':''}; при диапазоне мест — для каждого участника${e.teams?' или команды':''} в нём.</p>`:'<p class="tournament-note">Распределение призовых не указано.</p>'}
      <h3>Участники и результаты <span class="muted" id="t-participant-count"></span></h3><div class="tournament-player-tools"><input id="t-player-search" type="search" placeholder="Найти участника…" aria-label="Найти участника турнира"><select id="t-stage" aria-label="Этап турнира"><option value="all">Все этапы</option>${stages.groups.map(g=>`<option value="${g.idx}">${esc(g.label)} · ${g.members.length} уч.</option>`).join('')}</select></div><div id="t-player-list" class="tournament-player-list"></div>`;
    if(stages.groups.length>1&&stages.main)$('t-stage').value=String(stages.main.idx);
    $('t-stage').hidden=stages.groups.length<=1;
    $('t-stage').addEventListener('change',renderParticipants);$('t-player-search').addEventListener('input',renderParticipants);renderParticipants();
    if(!dialog.open)dialog.showModal();dialog.scrollTop=0;document.body.style.overflow='hidden';$('tournament-close').focus();
  }
  $('players-tab').addEventListener('click',()=>setView('players'));
  $('tournaments-tab').addEventListener('click',()=>setView('tournaments'));
  for(const id of ['t-search','t-year','t-mode','t-map','t-sort'])$(id).addEventListener(id==='t-search'?'input':'change',render);
  $('t-reset').addEventListener('click',()=>{for(const id of ['t-search','t-year','t-mode','t-map'])$(id).value='';$('t-sort').value='newest';render();});
  $('maps-more').addEventListener('click',()=>{allMaps=!allMaps;render();});
  $('tournaments-view').addEventListener('click',event=>{
    const map=event.target.closest('[data-map]');if(map){chooseMap(map.dataset.map);return;}
    const row=event.target.closest('[data-cup]');if(row)open(row.dataset.cup);
  });
  $('tournament-body').addEventListener('click',event=>{
    const map=event.target.closest('[data-map]');if(map){chooseMap(map.dataset.map);return;}
    const button=event.target.closest('[data-open-player]');if(button){dialog.close();page.openPlayer(button.dataset.openPlayer);}
  });
  $('tournament-about').addEventListener('click',page.openAbout);
  $('tournament-close').addEventListener('click',()=>dialog.close());
  dialog.addEventListener('close',()=>{current=null;page.syncScrollLock();});
  dialog.addEventListener('click',event=>{if(event.target!==dialog)return;const b=dialog.getBoundingClientRect();if(event.clientX<b.left||event.clientX>b.right||event.clientY<b.top||event.clientY>b.bottom)dialog.close();});
  window.RusaocTournaments={open,current:()=>current,catalog};
  render();
  if(location.hash==='#tournaments')setView('tournaments',false);
  const deepLink=location.hash.match(/^#tournament-(\d+)$/);if(deepLink){setView('tournaments',false);open(deepLink[1]);}
})();
