"""Representative SoundCloud markup for the DOM extractor tests.

Hand-written from the shapes SoundCloud's listing pages use. The point is not
byte fidelity -- it is that the extractor copes with their class names, with
markup carrying none of those names, and with the non-track links that share
the same two-segment URL shape.
"""

LIKES_PAGE = """
<!doctype html><html><body>
<header class="header">
  <a class="header__userNavUsernameButton" href="/you">me</a>
  <nav><a href="/discover">Discover</a><a href="/you/likes">Likes</a></nav>
</header>
<ul class="soundList">
  <li class="soundList__item">
    <div class="sound__header">
      <a class="soundTitle__title" href="/burial/archangel"><span>Archangel</span></a>
      <a class="soundTitle__username" href="/burial">Hyperdub</a>
    </div>
    <div class="sound__duration"><span class="sc-visuallyhidden">3:57</span></div>
    <button class="sc-button-like sc-button-selected" aria-pressed="true"
            aria-label="Unlike"></button>
  </li>
  <li class="soundList__item">
    <div class="sound__header">
      <a class="soundTitle__title" href="/fredagain/delilah-skrillex-remix">
        Fred again.. - Delilah (Skrillex Remix)</a>
      <a class="soundTitle__username" href="/fredagain">Fred again..</a>
    </div>
    <div class="sound__duration"><span class="sc-visuallyhidden">3:35</span></div>
    <button class="sc-button-like" aria-pressed="false" aria-label="Like"></button>
  </li>
  <li class="soundList__item">
    <div class="sound__header">
      <a class="soundTitle__title" href="/someonelse/a-ninety-minute-set">
        Live From The Warehouse</a>
      <a class="soundTitle__username" href="/someonelse">Some DJ</a>
    </div>
    <div class="sound__duration"><span class="sc-visuallyhidden">1:32:10</span></div>
  </li>
  <li class="soundList__item">
    <!-- A profile sub-page link: two segments, but not a track. -->
    <div class="sound__header">
      <a class="soundTitle__title" href="/burial/sets">Burial's albums</a>
      <a class="soundTitle__username" href="/burial">Hyperdub</a>
    </div>
  </li>
</ul>
</body></html>
"""

# No SoundCloud class names at all: the structural fallback has to carry it.
GENERIC_PAGE = """
<!doctype html><html><body>
<ul>
  <li><a href="/floating-points/last-bloom">Last Bloom</a>
      <a href="/floating-points">Ninja Tune</a><span>6:30</span></li>
  <li><a href="/bicep/glue">Glue</a>
      <a href="/bicep">Ninja Tune</a><span>4:31</span></li>
  <li><a href="/m83/midnight-city">Midnight City</a>
      <a href="/m83">M83</a><span>4:04</span></li>
</ul>
</body></html>
"""

EMPTY_PAGE = "<!doctype html><html><body><p>Nothing here yet.</p></body></html>"


def track_page(*, liked: bool, readable: bool = True) -> str:
    """A track page whose like button really toggles when clicked."""
    pressed = "true" if liked else "false"
    label = "Unlike" if liked else "Like"
    if not readable:
        return """
        <!doctype html><html><body>
          <div class="listenEngagement"><p>no like control here</p></div>
        </body></html>
        """
    return f"""
    <!doctype html><html><body>
      <div class="listenEngagement">
        <button class="sc-button-like{' sc-button-selected' if liked else ''}"
                aria-pressed="{pressed}" aria-label="{label}"
                onclick="
                  const p = this.getAttribute('aria-pressed') === 'true';
                  this.setAttribute('aria-pressed', p ? 'false' : 'true');
                  this.setAttribute('aria-label', p ? 'Like' : 'Unlike');
                  this.className = 'sc-button-like' + (p ? '' : ' sc-button-selected');
                ">{label}</button>
      </div>
    </body></html>
    """
