/* Filtering and sorting for the recipe browse page.
 *
 * Every card is already in the DOM, so the page works with JavaScript off;
 * this only hides, reorders and counts what is already there. Active filters
 * live in the URL hash so a filtered view can be linked to and shared.
 */
(function () {
	'use strict';

	var grid = document.querySelector('.recipe-grid');
	if (!grid) return;

	var cards = Array.prototype.slice.call(grid.querySelectorAll('.rcard'));
	var search = document.getElementById('q');
	var sortSelect = document.getElementById('sort');
	var reset = document.querySelector('.browse .reset');
	var countEl = document.querySelector('.result-count');
	var emptyEl = document.querySelector('.no-results');
	var facets = Array.prototype.slice.call(document.querySelectorAll('.facet'));
	var total = cards.length;

	// facet name -> Set of selected values. "without" is an exclusion filter.
	var selected = {};
	facets.forEach(function (facet) {
		selected[facet.dataset.facet] = new Set();
	});

	var terms = [];

	function splitList(value) {
		return value ? value.split('|') : [];
	}

	function matches(card) {
		for (var i = 0; i < terms.length; i++) {
			if (card.dataset.search.indexOf(terms[i]) === -1) return false;
		}

		var names = Object.keys(selected);
		for (var j = 0; j < names.length; j++) {
			var name = names[j];
			var chosen = selected[name];
			if (!chosen.size) continue;

			var values = name === 'tag' || name === 'without'
				? splitList(card.dataset[name])
				: [card.dataset[name]];

			var hit = values.some(function (value) { return chosen.has(value); });
			// "without" excludes: a hit disqualifies the card.
			if (name === 'without' ? hit : !hit) return false;
		}
		return true;
	}

	function compare(mode) {
		return function (a, b) {
			if (mode === 'time' || mode === 'calories') {
				var key = mode === 'time' ? 'time' : 'calories';
				var av = parseInt(a.dataset[key], 10) || 0;
				var bv = parseInt(b.dataset[key], 10) || 0;
				// Cards with no recorded value sort last rather than first.
				if (!av) av = Infinity;
				if (!bv) bv = Infinity;
				if (av !== bv) return av - bv;
			}
			return a.dataset.title.localeCompare(b.dataset.title);
		};
	}

	function anyFilterActive() {
		if (terms.length) return true;
		return Object.keys(selected).some(function (name) { return selected[name].size > 0; });
	}

	function apply() {
		var shown = 0;
		cards.forEach(function (card) {
			var visible = matches(card);
			card.hidden = !visible;
			if (visible) shown++;
		});

		var order = sortSelect ? sortSelect.value : 'title';
		cards.slice().sort(compare(order)).forEach(function (card) {
			grid.appendChild(card);
		});

		var active = anyFilterActive();
		if (countEl) {
			countEl.textContent = active
				? 'Showing ' + shown + ' of ' + total + ' recipes'
				: 'Showing all ' + total + ' recipes';
		}
		if (emptyEl) emptyEl.hidden = shown !== 0;
		if (reset) reset.hidden = !active;
	}

	function writeHash() {
		var parts = [];
		Object.keys(selected).forEach(function (name) {
			if (selected[name].size) {
				parts.push(name + '=' + Array.from(selected[name]).map(encodeURIComponent).join(','));
			}
		});
		if (search && search.value.trim()) {
			parts.push('q=' + encodeURIComponent(search.value.trim()));
		}
		var hash = parts.join('&');
		var bare = location.pathname + location.search;
		history.replaceState(null, '', hash ? '#' + hash : bare);
	}

	function readHash() {
		var hash = location.hash.replace(/^#/, '');
		Object.keys(selected).forEach(function (name) { selected[name].clear(); });
		if (search) search.value = '';

		hash.split('&').filter(Boolean).forEach(function (pair) {
			var eq = pair.indexOf('=');
			if (eq === -1) return;
			var name = pair.slice(0, eq);
			var value = decodeURIComponent(pair.slice(eq + 1));
			if (name === 'q') {
				if (search) search.value = value;
			} else if (selected[name]) {
				value.split(',').forEach(function (item) {
					selected[name].add(decodeURIComponent(item));
				});
			}
		});

		terms = search && search.value.trim()
			? search.value.trim().toLowerCase().split(/\s+/)
			: [];
		syncChips();
	}

	function syncChips() {
		facets.forEach(function (facet) {
			var chosen = selected[facet.dataset.facet];
			facet.querySelectorAll('.chip').forEach(function (chip) {
				var on = chosen.has(chip.dataset.value);
				chip.classList.toggle('on', on);
				chip.setAttribute('aria-pressed', on ? 'true' : 'false');
			});
		});
	}

	facets.forEach(function (facet) {
		var name = facet.dataset.facet;
		facet.addEventListener('click', function (event) {
			var chip = event.target.closest('.chip');
			if (!chip) return;
			var value = chip.dataset.value;
			if (selected[name].has(value)) {
				selected[name].delete(value);
			} else {
				selected[name].add(value);
			}
			syncChips();
			writeHash();
			apply();
		});
	});

	if (search) {
		var pending;
		search.addEventListener('input', function () {
			clearTimeout(pending);
			pending = setTimeout(function () {
				var value = search.value.trim().toLowerCase();
				terms = value ? value.split(/\s+/) : [];
				writeHash();
				apply();
			}, 120);
		});
	}

	if (sortSelect) sortSelect.addEventListener('change', apply);

	if (reset) {
		reset.addEventListener('click', function () {
			Object.keys(selected).forEach(function (name) { selected[name].clear(); });
			if (search) search.value = '';
			terms = [];
			syncChips();
			history.replaceState(null, '', location.pathname + location.search);
			apply();
		});
	}

	window.addEventListener('hashchange', function () {
		readHash();
		apply();
	});

	readHash();
	apply();
})();
