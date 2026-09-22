# The Family Recipe Box

A Jekyll site for the family recipe card collection, published at
<https://mattwatier.github.io/RecipeHtml>.

Built on the [Treat](https://github.com/wangonya/treat-jekyll-template) template
by Kanyi Wangonya.

## How it fits together

The recipes are **not** written here. The source of truth is the Obsidian vault
at `../Recipe`, where each card lives as a note with a structured `kaper` block.
`tools/build_recipes.py` reads that vault and generates:

- `_recipes/*.md` — one page per recipe, front matter only, no body
- `_data/facets.yml` — the filter options and counts used by the browse page

Both are committed, so the GitHub Actions build never needs the vault.

## Regenerating after editing the vault

```sh
python3 tools/build_recipes.py          # vault assumed at ../Recipe
python3 tools/build_recipes.py --check  # report only, write nothing
```

`--vault PATH` points at a vault somewhere else. The script rewrites `_recipes/`
from scratch each run, so deleting a note in the vault removes its page here.

## Running locally

```sh
bundle install
bundle exec jekyll serve
```

Then open <http://localhost:4000/RecipeHtml/>. The `baseurl` in `_config.yml`
matches the GitHub Pages project path, so the trailing path is needed locally too.

## Deploying

Pushing to `main` triggers `.github/workflows/pages.yml`, which builds with
Jekyll and publishes to GitHub Pages. In the repository settings, **Pages →
Source** must be set to **GitHub Actions**.

## Layout

| Path | What it is |
| --- | --- |
| `tools/build_recipes.py` | Vault → Jekyll generator |
| `_recipes/` | Generated; do not edit by hand |
| `_data/facets.yml` | Generated; do not edit by hand |
| `_layouts/recipe.html` | Single recipe page |
| `recipes.html` | Browse page markup and facet controls |
| `js/browse.js` | Client-side search, filtering and sorting |
| `_includes/recipe-schema.html` | Schema.org Recipe structured data |
