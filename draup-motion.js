/* draup.com's motion, rebuilt on GSAP (ScrollTrigger and SplitText are free
   since GSAP 3.13). The choreography is draup.com's; the fades run about
   twice as fast, eased out, since theirs (a second or two, eased in) left
   content looking unloaded here:

   - Headings and blocks marked data-reveal fade in over half a second the
     moment their top crosses the bottom of the screen.
   - The hero name drops in word by word, then its subheading does the same.
   - The About statement lights up letter by letter as it scrolls past.
   - The journey draws a line across each stop in turn.
   - Figures marked data-count count up from zero over two seconds when seen.
   - The navigation turns dark over a black section.
   - "Where the deep work shows up" pins while its backdrop zooms past (one of
     four, a different one each visit), then turns black and brings in the
     picks.

   With reduced motion, or if GSAP failed to load, everything is just shown. */

(function () {
    const root = document.documentElement;
    const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
    const show = () => root.classList.remove('motion-pending');

    // Each part on its own: one failing must not leave the rest hidden.
    const attempt = (part) => {
        try {
            part();
        } catch (error) {
            console.warn('draup-motion:', part.name, error);
        }
    };

    attempt(setupNavVariant);
    attempt(pauseOffscreenAurora);

    if (reduced || !window.gsap || !window.ScrollTrigger) {
        attempt(staticPress);
        show();
        return;
    }

    gsap.registerPlugin(ScrollTrigger);
    if (window.SplitText) gsap.registerPlugin(SplitText);

    attempt(revealOnScroll);
    attempt(heroIntro);
    attempt(textReveal);
    attempt(drawJourney);
    attempt(countUp);
    attempt(pressZoom);
    show();

    // Webfonts change line lengths, and so where everything sits.
    if (document.fonts && document.fonts.ready) {
        document.fonts.ready.then(() => ScrollTrigger.refresh());
    }

    // ---------------------------------------------------------------------

    function revealOnScroll() {
        const blocks = gsap.utils.toArray('[data-reveal]');
        gsap.set(blocks, { opacity: 0 });
        ScrollTrigger.batch(blocks, {
            start: 'top bottom',
            once: true,
            onEnter: (batch) => batch.forEach((block, index) => {
                gsap.to(block, {
                    opacity: 1,
                    duration: 0.5,
                    ease: 'power2.out',
                    delay: (Number(block.dataset.revealDelay) || 0) / 1000 + index * 0.05,
                    overwrite: true,
                });
            }),
        });
    }

    function heroIntro() {
        const name = document.querySelector('.hero-display-name');
        const subheads = gsap.utils.toArray('.hero-lead, .hero-bio');
        const parts = gsap.utils.toArray('.hero-main-top, .hero-intro-line, .hero-focus-pills, .hero-actions, .hero-socials');
        if (!name) return;
        const timeline = gsap.timeline();
        timeline.fromTo(parts, { opacity: 0 }, { opacity: 1, duration: 0.6, ease: 'power2.out', stagger: 0.08 }, 0.2);

        if (!window.SplitText) {
            timeline.fromTo([name, ...subheads], { opacity: 0 }, { opacity: 1, duration: 0.6, ease: 'power2.out' }, 0);
            return;
        }
        // The name's shimmer pauses while its words move (see .dr-intro).
        name.classList.add('dr-intro');
        const head = SplitText.create(name.querySelectorAll('.dn-line'), { type: 'words' });
        const sub = SplitText.create(subheads, { type: 'words' });
        gsap.set([name, ...subheads], { opacity: 1 });
        timeline
            .from(head.words, { opacity: 0, yPercent: -100, duration: 0.8, ease: 'power2.out', stagger: { amount: 0.3 } }, 0)
            .from(sub.words, { opacity: 0, yPercent: -100, duration: 0.8, ease: 'power2.out', stagger: { amount: 0.4 } }, 0.35)
            .eventCallback('onComplete', () => {
                head.revert();
                sub.revert();
                name.classList.remove('dr-intro');
            });
    }

    function textReveal() {
        if (!window.SplitText) return;
        gsap.utils.toArray('[data-text-reveal]').forEach((statement) => {
            SplitText.create(statement, {
                type: 'words,chars',
                autoSplit: true,
                // Faded rather than recoloured, so it ends right in either theme.
                onSplit: (self) => gsap.from(self.chars, {
                    opacity: 0.2,
                    ease: 'power2.out',
                    stagger: 0.04,
                    // Lit by the time it reaches the middle of the screen.
                    scrollTrigger: { trigger: statement, start: 'top 90%', end: 'center 60%', scrub: 0.5 },
                }),
            });
        });
    }

    function drawJourney() {
        const stops = gsap.utils.toArray('.hero-journey-list .journey-stop');
        if (!stops.length) return;
        const lines = stops.map((stop) => {
            const line = document.createElement('span');
            line.className = 'journey-line';
            line.setAttribute('aria-hidden', 'true');
            stop.prepend(line);
            return line;
        });
        const contents = stops.map((stop) => [...stop.children].filter((child) => !child.classList.contains('journey-line')));
        gsap.set(lines, { scaleX: 0 });
        gsap.set(contents.flat(), { opacity: 0 });
        const timeline = gsap.timeline({
            scrollTrigger: { trigger: '.hero-journey-list', start: 'top 85%', once: true },
        });
        // draup.com's timeline steps: each line draws, its stop fades in after.
        stops.forEach((stop, index) => {
            const at = index * 0.25;
            timeline
                .to(lines[index], { scaleX: 1, duration: index ? 0.4 : 0.3, ease: 'none' }, at)
                .to(contents[index], { opacity: 1, duration: 0.4, ease: 'power2.out' }, at + 0.15);
        });
    }

    function countUp() {
        gsap.utils.toArray('[data-count]').forEach((figure) => {
            const end = Number(figure.dataset.count);
            const counter = { value: 0 };
            figure.textContent = '0';
            gsap.to(counter, {
                value: end,
                duration: 2,
                ease: 'none',
                onUpdate: () => { figure.textContent = String(Math.round(counter.value)); },
                scrollTrigger: { trigger: figure, start: 'top 90%', once: true },
            });
        });
    }

    function pressZoom() {
        const press = document.querySelector('.dr-press');
        if (!press) return;
        const media = gsap.matchMedia();
        media.add('(min-width: 768px)', () => {
            press.classList.remove('is-static');
            press.dataset.bg = 'light';
            const scene = press.querySelector('.dr-press-scene');
            if (!scene.dataset.backdrop) pickBackdrop(scene);
            const overlay = press.querySelector('.dr-press-overlay');
            const heading = press.querySelector('.dr-press-heading');
            const title = press.querySelector('.dr-press-title');
            const picks = press.querySelector('.dr-press-picks');
            // The hidden picks still take room below the title, which left it
            // sitting high over the backdrop: it starts centred and rises as
            // they come in.
            const lift = () => (picks.offsetHeight + parseFloat(getComputedStyle(picks).marginTop)) / 2;
            const timeline = gsap.timeline({
                defaults: { ease: 'none' },
                scrollTrigger: {
                    trigger: press,
                    start: 'top top',
                    end: 'bottom bottom',
                    scrub: 1,
                    invalidateOnRefresh: true,
                    onUpdate: (self) => {
                        const dark = self.progress > 0.62;
                        if ((press.dataset.bg === 'dark') !== dark) {
                            press.dataset.bg = dark ? 'dark' : 'light';
                            if (window.drNavUpdate) window.drNavUpdate();
                        }
                        picks.classList.toggle('is-live', self.progress > 0.8);
                    },
                },
            });
            // draup.com's press timeline, on the same 0-1 canvas.
            timeline
                .fromTo(scene, { scale: 1 }, { scale: 2.6, duration: 0.91 }, 0)
                .to(overlay, { opacity: 1, duration: 0.3 }, 0.5)
                // From an explicit 1: the variable has no value of its own to start from.
                .fromTo(heading, { '--dr-press-glow': 1 }, { '--dr-press-glow': 0, duration: 0.3 }, 0.5)
                .to(title, { color: '#FFFFFF', duration: 0.09 }, 0.69)
                .fromTo(title, { y: lift }, { y: 0, duration: 0.15, ease: 'power1.inOut' }, 0.7)
                .fromTo(picks, { opacity: 0, scale: 0.5 }, { opacity: 1, scale: 1, duration: 0.15 }, 0.72)
                .set({}, {}, 1);
            return () => { press.dataset.bg = 'light'; };
        });
        media.add('(max-width: 767px)', staticPress);
    }

    // A different backdrop from last visit's, chosen at random from the rest:
    // the grid of cards, or one of the images.
    function pickBackdrop(scene) {
        const backdrops = scene.dataset.backdrops.trim().split(/\s+/);
        const key = 'dr-press-backdrop';
        let last = -1;
        try {
            last = backdrops.indexOf(localStorage.getItem(key));
        } catch (error) { /* storage blocked: any of them will do */ }
        let pick = Math.floor(Math.random() * (last < 0 ? backdrops.length : backdrops.length - 1));
        if (last >= 0 && pick >= last) pick += 1;
        const backdrop = backdrops[pick];
        scene.dataset.backdrop = backdrop;
        if (backdrop === 'cards') {
            scene.querySelector('.dr-press-grid').hidden = false;
        } else {
            const image = scene.querySelector('.dr-press-image');
            image.addEventListener('load', () => image.classList.add('is-loaded'), { once: true });
            image.src = backdrop;
        }
        try {
            localStorage.setItem(key, backdrop);
        } catch (error) { /* as above */ }
    }

    function staticPress() {
        const press = document.querySelector('.dr-press');
        if (!press) return;
        press.classList.add('is-static');
        press.dataset.bg = 'dark';
        if (window.drNavUpdate) window.drNavUpdate();
    }

    // The navigation takes the colour of the section beneath it, judged at
    // the height it sits (draup.com watches the same 60px band).
    function setupNavVariant() {
        const navbar = document.querySelector('.navbar');
        if (!navbar) return;
        let current = null;
        let queued = false;
        const update = () => {
            queued = false;
            const under = [...document.querySelectorAll('[data-bg]')].find((section) => {
                const box = section.getBoundingClientRect();
                return box.top <= 60 && box.bottom > 60;
            });
            const variant = under ? under.dataset.bg : 'light';
            if (variant !== current) {
                current = variant;
                navbar.dataset.navVariant = variant;
            }
        };
        const schedule = () => {
            if (!queued) {
                queued = true;
                requestAnimationFrame(update);
            }
        };
        addEventListener('scroll', schedule, { passive: true });
        addEventListener('resize', schedule);
        window.drNavUpdate = schedule;
        update();
    }

    function pauseOffscreenAurora() {
        if (!('IntersectionObserver' in window)) return;
        const watcher = new IntersectionObserver((entries) => {
            entries.forEach((entry) => entry.target.classList.toggle('is-paused', !entry.isIntersecting));
        });
        document.querySelectorAll('.dr-aurora').forEach((aurora) => watcher.observe(aurora));
    }
})();
