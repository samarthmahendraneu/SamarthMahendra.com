document.addEventListener('DOMContentLoaded', () => {
    const header = document.getElementById('site-header');
    const progressBar = document.getElementById('page-progress-bar');
    const menuToggle = document.getElementById('menu-toggle');
    const navigation = document.getElementById('primary-navigation');
    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const heroImages = Array.from(document.querySelectorAll('.hero-image'));

    if (!reduceMotion && heroImages.length > 1) {
        let activeHeroIndex = heroImages.findIndex(image => image.classList.contains('is-active'));
        let heroStepCount = 0;
        const fastHeroDelay = 2800;
        const slowHeroDelay = 7400;
        const fastHeroSteps = heroImages.length * 2;
        if (activeHeroIndex < 0) activeHeroIndex = 0;

        function advanceHero() {
            heroImages[activeHeroIndex].classList.remove('is-active');
            activeHeroIndex = (activeHeroIndex + 1) % heroImages.length;
            heroImages[activeHeroIndex].classList.add('is-active');
            heroStepCount += 1;
            window.setTimeout(advanceHero, heroStepCount < fastHeroSteps ? fastHeroDelay : slowHeroDelay);
        }

        window.setTimeout(advanceHero, fastHeroDelay);
    }

    function updatePageChrome() {
        const scrollable = document.documentElement.scrollHeight - window.innerHeight;
        const progress = scrollable > 0 ? window.scrollY / scrollable : 0;
        progressBar.style.width = `${Math.min(1, Math.max(0, progress)) * 100}%`;
        header.classList.toggle('is-scrolled', window.scrollY > 40);
    }

    let pageFrame = null;
    window.addEventListener('scroll', () => {
        if (pageFrame) return;
        pageFrame = requestAnimationFrame(() => {
            updatePageChrome();
            updateEvolution();
            pageFrame = null;
        });
    }, { passive: true });

    updatePageChrome();

    function closeMenu() {
        document.body.classList.remove('menu-open');
        header.classList.remove('menu-active');
        navigation.classList.remove('is-open');
        menuToggle.setAttribute('aria-expanded', 'false');
    }

    menuToggle.addEventListener('click', () => {
        const isOpen = menuToggle.getAttribute('aria-expanded') === 'true';
        if (isOpen) {
            closeMenu();
            return;
        }
        document.body.classList.add('menu-open');
        header.classList.add('menu-active');
        navigation.classList.add('is-open');
        menuToggle.setAttribute('aria-expanded', 'true');
    });

    navigation.querySelectorAll('a').forEach(link => link.addEventListener('click', closeMenu));

    const revealItems = document.querySelectorAll('.reveal');
    if (reduceMotion || !('IntersectionObserver' in window)) {
        revealItems.forEach(item => item.classList.add('is-visible'));
    } else {
        const revealObserver = new IntersectionObserver(entries => {
            entries.forEach(entry => {
                if (!entry.isIntersecting) return;
                entry.target.classList.add('is-visible');
                revealObserver.unobserve(entry.target);
            });
        }, { rootMargin: '0px 0px -9% 0px', threshold: 0.08 });
        revealItems.forEach(item => revealObserver.observe(item));
    }

    const evolution = document.querySelector('.evolution-scroll');
    const plotFrames = Array.from(document.querySelectorAll('.plot-frame'));
    const stageRail = Array.from(document.querySelectorAll('.stage-rail span'));
    const stageCurrent = document.getElementById('stage-current');
    const stageTitle = document.getElementById('stage-title');
    const stageDescription = document.getElementById('stage-description');
    const stages = [
        {
            title: 'Read the land',
            description: 'Begin with the existing coffee estate, its access, tree cover, long views, and monsoon drainage.'
        },
        {
            title: 'Place the house',
            description: 'A non-rectangular footprint settles into the long plot, keeping living spaces close to garden edges.'
        },
        {
            title: 'Carve outdoor rooms',
            description: 'Pool, koi pond, private sit-outs, and event lawn become extensions of the ground-floor program.'
        },
        {
            title: 'Connect the estate',
            description: 'Paths, planting, covered thresholds, and the arrival court stitch architecture back into the plantation.'
        },
        {
            title: 'Complete the vision',
            description: 'House and landscape read as one resort-like composition — phased in construction, unified in intent.'
        }
    ];
    let activeStage = -1;

    function setStage(index) {
        if (index === activeStage) return;
        activeStage = index;
        plotFrames.forEach((frame, frameIndex) => frame.classList.toggle('is-active', frameIndex === index));
        stageRail.forEach((rail, railIndex) => rail.classList.toggle('is-active', railIndex === index));
        stageCurrent.textContent = String(index + 1).padStart(2, '0');
        stageTitle.textContent = stages[index].title;
        stageDescription.textContent = stages[index].description;
    }

    function updateEvolution() {
        if (!evolution) return;
        const rect = evolution.getBoundingClientRect();
        const scrollDistance = Math.max(1, rect.height - window.innerHeight);
        const progress = Math.min(1, Math.max(0, -rect.top / scrollDistance));
        const nextStage = Math.min(stages.length - 1, Math.floor(progress * stages.length));
        setStage(nextStage);
    }

    updateEvolution();
    window.addEventListener('resize', updateEvolution, { passive: true });

    const model = document.getElementById('estate-model');
    const modelShell = document.getElementById('model-shell');
    const modelError = document.getElementById('model-error');
    const modelFullscreen = document.getElementById('model-fullscreen');
    const modelPosterButton = model.querySelector('[slot="poster"]');
    const modelVariantName = document.getElementById('model-variant-name');
    const modelPolygonCount = document.getElementById('model-polygon-count');
    const modelVariantButtons = Array.from(document.querySelectorAll('[data-model-variants] button'));
    const isWebProtocol = window.location.protocol === 'http:' || window.location.protocol === 'https:';

    if (isWebProtocol) {
        model.setAttribute('src', model.dataset.src);
        model.dataset.activeSrc = model.dataset.src;
    } else {
        modelError.hidden = false;
        modelError.textContent = 'Interactive 3D requires a local web server. Open http://127.0.0.1:8000/house.html instead of opening this file directly.';
        modelPosterButton.textContent = 'Open this page over HTTP to view 3D';
        modelPosterButton.disabled = true;
        modelVariantButtons.forEach(button => { button.disabled = true; });
    }

    modelPosterButton.addEventListener('click', () => {
        if (typeof model.dismissPoster === 'function') model.dismissPoster();
    });

    modelVariantButtons.forEach(button => {
        button.addEventListener('click', () => {
            const nextSource = button.dataset.modelSrc;
            if (!isWebProtocol || nextSource === model.dataset.activeSrc) return;

            modelShell.classList.add('is-switching');
            modelError.hidden = true;
            model.autoRotate = false;
            modelVariantName.textContent = `${button.dataset.modelName} · loading`;
            modelPolygonCount.textContent = button.dataset.modelPolygons;
            modelVariantButtons.forEach(item => {
                item.disabled = true;
                item.classList.toggle('is-active', item === button);
            });

            model.dataset.activeSrc = nextSource;
            model.setAttribute('src', nextSource);
        });
    });

    document.querySelectorAll('[data-model-views] button').forEach(button => {
        button.addEventListener('click', () => {
            model.cameraOrbit = button.dataset.orbit;
            if (typeof model.jumpCameraToGoal === 'function') model.jumpCameraToGoal();
            document.querySelectorAll('[data-model-views] button').forEach(item => item.classList.toggle('is-active', item === button));
        });
    });

    document.querySelectorAll('[data-model-light] button').forEach(button => {
        button.addEventListener('click', () => {
            model.exposure = Number(button.dataset.exposure);
            document.querySelectorAll('[data-model-light] button').forEach(item => item.classList.toggle('is-active', item === button));
        });
    });

    modelFullscreen.addEventListener('click', async () => {
        try {
            if (!document.fullscreenElement) {
                await modelShell.requestFullscreen();
            } else {
                await document.exitFullscreen();
            }
        } catch (error) {
            modelFullscreen.setAttribute('title', 'Fullscreen is not available in this browser');
        }
    });

    model.addEventListener('load', () => {
        const activeButton = modelVariantButtons.find(button => button.dataset.modelSrc === model.dataset.activeSrc);
        if (activeButton) {
            modelVariantName.textContent = activeButton.dataset.modelName;
            modelPolygonCount.textContent = activeButton.dataset.modelPolygons;
        }
        modelShell.classList.remove('is-switching');
        modelVariantButtons.forEach(button => { button.disabled = false; });
        model.autoRotate = !reduceMotion;
    });

    model.addEventListener('error', () => {
        modelError.hidden = false;
        modelShell.classList.remove('is-switching');
        modelVariantButtons.forEach(button => { button.disabled = false; });
    });

    const roomGalleries = {
        living: [
            { src: 'house-assets/images/living-space.webp', alt: 'Great room with warm stone and estate-facing glass', title: 'Great room / formal living' },
            { src: 'house-assets/images/room-refs/living-01.jpg', alt: 'Living room reference with warm materials', title: 'Living room reference 01' },
            { src: 'house-assets/images/room-refs/living-02.jpg', alt: 'Living room reference with layered lighting', title: 'Living room reference 02' }
        ],
        family: [
            { src: 'house-assets/images/family-lounge.webp', alt: 'Family lounge with dark timber and warm lighting', title: 'Family lounge' },
            { src: 'house-assets/images/room-refs/family-01.jpg', alt: 'Family lounge reference', title: 'Family lounge reference 01' }
        ],
        dining: [
            { src: 'house-assets/images/dining.webp', alt: 'Formal dining room with dark walnut and chandelier', title: 'Formal dining' },
            { src: 'house-assets/images/room-refs/dining-01.jpg', alt: 'Formal dining reference 01', title: 'Formal dining reference 01' },
            { src: 'house-assets/images/room-refs/dining-02.jpg', alt: 'Formal dining reference 02', title: 'Formal dining reference 02' },
            { src: 'house-assets/images/room-refs/dining-03.jpg', alt: 'Formal dining reference 03', title: 'Formal dining reference 03' },
            { src: 'house-assets/images/room-refs/dining-04.jpg', alt: 'Formal dining reference 04', title: 'Formal dining reference 04' }
        ],
        kitchen: [
            { src: 'house-assets/images/room-refs/kitchen-02.jpg', alt: 'Kitchen reference with warm cabinetry', title: 'Kitchen reference 02' },
            { src: 'house-assets/images/kitchen.webp', alt: 'Statement kitchen with large stone island', title: 'Main kitchen' },
            { src: 'house-assets/images/room-refs/kitchen-01.jpg', alt: 'Kitchen reference with large island', title: 'Kitchen reference 01' }
        ],
        suites: [
            { src: 'house-assets/images/bedroom.webp', alt: 'Suite bedroom with timber ceiling and warm cove light', title: 'Suites' },
            { src: 'house-assets/images/room-refs/bedroom-01.jpg', alt: 'Suite bedroom reference', title: 'Suite reference 01' },
            { src: 'house-assets/images/room-refs/bedroom-02.jpg', alt: 'Suite bedroom reference with warm neutral palette', title: 'Suite reference 02' }
        ],
        bathroom: [
            { src: 'house-assets/images/room-refs/bathroom-01.jpg', alt: 'Bathroom reference 01', title: 'Bathroom reference 01' },
            { src: 'house-assets/images/room-refs/bathroom-02.jpg', alt: 'Bathroom reference 02', title: 'Bathroom reference 02' },
            { src: 'house-assets/images/room-refs/bathroom-03.jpg', alt: 'Bathroom reference 03', title: 'Bathroom reference 03' },
            { src: 'house-assets/images/room-refs/bathroom-04.jpg', alt: 'Common bath area reference', title: 'Common bath area' },
            { src: 'house-assets/images/bathroom.webp', alt: 'Natural stone bathroom with warm wood vanity', title: 'Bathrooms' }
        ],
        closet: [
            { src: 'house-assets/images/closet.webp', alt: 'Walk-in closet with warm fitted joinery', title: 'Walk-in closets' },
            { src: 'house-assets/images/room-refs/closet-01.jpg', alt: 'Walk-in closet reference', title: 'Walk-in closet reference 01' }
        ],
        stair: [
            { src: 'house-assets/images/staircase.webp', alt: 'Floating stair against dark stone wall', title: 'Foyer + stair' },
            { src: 'house-assets/images/room-refs/stair-01.jpg', alt: 'Staircase reference', title: 'Stair reference 01' }
        ],
        office: [
            { src: 'house-assets/images/office.webp', alt: 'Executive office with walnut built-ins', title: 'Executive office' },
            { src: 'house-assets/images/room-refs/office-01.jpg', alt: 'Executive office reference 01', title: 'Office reference 01' },
            { src: 'house-assets/images/room-refs/office-02.jpg', alt: 'Executive office reference 02', title: 'Office reference 02' }
        ],
        bar: [
            { src: 'house-assets/images/bar.webp', alt: 'Amber bar lounge with timber shelves', title: 'Bar lounge' },
            { src: 'house-assets/images/room-refs/bar-01.jpg', alt: 'Bar lounge reference', title: 'Bar lounge reference 01' }
        ],
        laundry: [
            { src: 'house-assets/images/room-refs/laundry-01.jpg', alt: 'Laundry room with warm fitted cabinetry', title: 'Laundry + mudroom' },
            { src: 'house-assets/images/room-refs/laundry-02.jpg', alt: 'Laundry room reference with built-in cabinetry', title: 'Laundry reference 02' }
        ],
        gym: [
            { src: 'house-assets/images/room-refs/gym-01.jpg', alt: 'Home gym and wellness room reference', title: 'Gym / wellness' }
        ],
        puja: [
            { src: 'house-assets/images/room-refs/puja-01.jpg', alt: 'Puja room reference with calm warm lighting', title: 'Puja room reference 01' },
            { src: 'house-assets/images/room-refs/puja-02.jpg', alt: 'Puja room reference with dedicated prayer wall', title: 'Puja room reference 02' }
        ]
    };

    const lightbox = document.getElementById('lightbox');
    const lightboxImage = document.getElementById('lightbox-image');
    const lightboxCaption = document.getElementById('lightbox-caption');
    const lightboxClose = document.getElementById('lightbox-close');
    const lightboxControls = document.getElementById('lightbox-gallery-controls');
    const lightboxCounter = document.getElementById('lightbox-counter');
    const lightboxPrev = document.getElementById('lightbox-prev');
    const lightboxNext = document.getElementById('lightbox-next');
    const roomGalleryPanel = document.getElementById('room-gallery-panel');
    const roomGalleryTitle = document.getElementById('room-gallery-title');
    const roomGalleryCount = document.getElementById('room-gallery-count');
    const roomGalleryPreview = document.getElementById('room-gallery-preview');
    const roomGalleryClose = document.getElementById('room-gallery-close');
    document.body.append(roomGalleryPanel);
    let activeGallery = [];
    let activeGalleryIndex = 0;

    function setRoomCardImage(card, index) {
        const gallery = roomGalleries[card.dataset.gallery];
        const image = card.querySelector('img');
        if (!gallery || !image) return;
        const nextImage = gallery[index % gallery.length];
        image.src = nextImage.src;
        image.alt = nextImage.alt;
        card.dataset.galleryIndex = String(index % gallery.length);
    }

    document.querySelectorAll('.room-reference[data-gallery]').forEach(card => {
        const gallery = roomGalleries[card.dataset.gallery];
        if (!gallery) return;

        const label = card.querySelector('span');
        if (label) label.textContent = `${gallery.length} ref${gallery.length === 1 ? '' : 's'}`;
        if (gallery.length > 1) {
            const count = document.createElement('b');
            count.className = 'room-count';
            count.textContent = `${gallery.length} images`;
            card.append(count);
        }

        let cycleTimer = null;
        let cycleIndex = 0;
        const startCycle = () => {
            if (reduceMotion || gallery.length < 2 || cycleTimer) return;
            cycleIndex = Number(card.dataset.galleryIndex || 0);
            cycleTimer = window.setInterval(() => {
                cycleIndex = (cycleIndex + 1) % gallery.length;
                setRoomCardImage(card, cycleIndex);
            }, 950);
        };
        const stopCycle = () => {
            if (cycleTimer) window.clearInterval(cycleTimer);
            cycleTimer = null;
            cycleIndex = 0;
            setRoomCardImage(card, 0);
        };

        card.addEventListener('pointerenter', startCycle);
        card.addEventListener('pointerleave', stopCycle);
        card.addEventListener('focus', startCycle);
        card.addEventListener('blur', stopCycle);
    });

    function updateLightbox() {
        const item = activeGallery[activeGalleryIndex];
        if (!item) return;
        lightboxImage.src = item.src;
        lightboxImage.alt = item.alt || '';
        lightboxCaption.textContent = item.title || item.alt || '';
        lightboxControls.hidden = activeGallery.length < 2;
        lightboxCounter.textContent = `${activeGalleryIndex + 1} / ${activeGallery.length}`;
    }

    function openLightbox(gallery, index = 0) {
        activeGallery = gallery;
        activeGalleryIndex = index;
        updateLightbox();
        lightbox.showModal();
    }

    function stepLightbox(direction) {
        if (activeGallery.length < 2) return;
        activeGalleryIndex = (activeGalleryIndex + direction + activeGallery.length) % activeGallery.length;
        updateLightbox();
    }

    function showRoomGallery(button, gallery) {
        roomGalleryTitle.textContent = button.querySelector('strong')?.textContent || 'Room gallery';
        roomGalleryCount.textContent = `${gallery.length} image${gallery.length === 1 ? '' : 's'}`;
        roomGalleryPreview.replaceChildren(...gallery.map((item, index) => {
            const previewButton = document.createElement('button');
            previewButton.type = 'button';
            previewButton.setAttribute('aria-label', `Open ${item.title || item.alt || 'reference image'}`);

            const image = document.createElement('img');
            image.src = item.src;
            image.alt = item.alt || '';
            image.loading = 'lazy';

            const label = document.createElement('span');
            label.textContent = item.title || `Reference ${index + 1}`;

            previewButton.append(image, label);
            previewButton.addEventListener('click', () => openLightbox(gallery, index));
            return previewButton;
        }));
        roomGalleryPanel.hidden = false;
        document.body.classList.add('gallery-open');
        roomGalleryClose.focus();
    }

    function closeRoomGallery() {
        roomGalleryPanel.hidden = true;
        document.body.classList.remove('gallery-open');
    }

    document.querySelectorAll('[data-lightbox]').forEach(button => {
        button.addEventListener('click', () => {
            const gallery = roomGalleries[button.dataset.gallery];
            if (gallery) {
                showRoomGallery(button, gallery);
                return;
            }

            const sourceImage = button.querySelector('img');
            openLightbox([{
                src: sourceImage.currentSrc || sourceImage.src,
                alt: sourceImage.alt,
                title: button.dataset.title || sourceImage.alt
            }]);
        });
    });

    lightboxPrev.addEventListener('click', () => stepLightbox(-1));
    lightboxNext.addEventListener('click', () => stepLightbox(1));
    lightboxClose.addEventListener('click', () => lightbox.close());
    lightbox.addEventListener('click', event => {
        if (event.target === lightbox) lightbox.close();
    });
    lightbox.addEventListener('keydown', event => {
        if (event.key === 'ArrowLeft') stepLightbox(-1);
        if (event.key === 'ArrowRight') stepLightbox(1);
    });
    roomGalleryClose.addEventListener('click', closeRoomGallery);
    roomGalleryPanel.addEventListener('click', event => {
        if (event.target === roomGalleryPanel) closeRoomGallery();
    });
    document.addEventListener('keydown', event => {
        if (event.key === 'Escape' && !roomGalleryPanel.hidden) closeRoomGallery();
    });
});
