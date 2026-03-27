document.addEventListener('DOMContentLoaded', function () {
    // Clean white theme — no zoom applied

    // Initialize AOS Animation
    AOS.init({
        duration: 800,
        easing: 'ease-out-cubic',
        once: true,
        offset: 50
    });

    // Update Year
    document.getElementById('year').textContent = new Date().getFullYear();

    // Mobile Menu Toggle
    const mobileToggle = document.querySelector('.mobile-toggle');
    const mobileMenu = document.querySelector('.mobile-menu');
    const mobileClose = document.querySelector('.mobile-close');
    const mobileLinks = document.querySelectorAll('.mobile-links a');

    function toggleMenu() {
        mobileMenu.classList.toggle('active');
        document.body.style.overflow = mobileMenu.classList.contains('active') ? 'hidden' : '';
    }

    if (mobileToggle) mobileToggle.addEventListener('click', toggleMenu);
    if (mobileClose) mobileClose.addEventListener('click', toggleMenu);

    mobileLinks.forEach(link => {
        link.addEventListener('click', toggleMenu);
    });

    // Chatbot Logic
    const chatbotToggle = document.getElementById('chatbot-toggle');
    const chatbotContainer = document.getElementById('chatbot-container');
    const chatbotClose = document.getElementById('chatbot-close');
    const chatbotInput = document.getElementById('chatbot-input');
    const chatbotSend = document.getElementById('chatbot-send');
    const chatbotMessages = document.getElementById('chatbot-messages');
    const starterPromptsContainer = document.getElementById('chatbot-starter-prompts');

    const SERVER_URL = 'https://samarthmahendra-github-io.onrender.com';
    let conversation = [];
    let chatUsername = null;

    // Toggle Chatbot
    // Toggle Chatbot Logic moved to refreshStarterPrompts section below

    // Send Message
    function sendMessage() {
        const message = chatbotInput.value.trim();
        if (!message) return;

        // Add User Message
        addMessage(message, 'user');
        chatbotInput.value = '';

        // Hide starter prompts
        if (starterPromptsContainer) starterPromptsContainer.style.display = 'none';

        // Show Typing Indicator
        showTypingIndicator();

        // Generate Username if needed
        if (!chatUsername) {
            chatUsername = 'user_' + Math.random().toString(36).substring(2, 10);
        }

        // Fetch Response
        fetch(`${SERVER_URL}/chat`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                message: message,
                conversation: conversation,
                username: chatUsername
            })
        })
            .then(response => response.json())
            .then(data => {
                removeTypingIndicator();
                conversation = data.conversation || conversation;
                if (data.output) addMessage(data.output, 'bot');
            })
            .catch(error => {
                removeTypingIndicator();
                addMessage("Sorry, I'm having trouble connecting to the server. Please try again later.", 'bot');
                console.error('Error:', error);
            });
    }

    chatbotSend.addEventListener('click', sendMessage);
    chatbotInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') sendMessage();
    });

    // Helper: Add Message
    function addMessage(text, sender) {
        const msgDiv = document.createElement('div');
        msgDiv.classList.add('message', `${sender}-message`);

        const contentDiv = document.createElement('div');
        contentDiv.classList.add('message-content');
        // First escape HTML, then convert newlines to <br>, then linkify
        contentDiv.innerHTML = linkify(nl2br(escapeHTML(text)));

        msgDiv.appendChild(contentDiv);
        chatbotMessages.appendChild(msgDiv);
        chatbotMessages.scrollTop = chatbotMessages.scrollHeight;
    }

    // Helper: Typing Indicator
    function showTypingIndicator() {
        const indicator = document.createElement('div');
        indicator.id = 'typing-indicator';
        indicator.classList.add('message', 'bot-message');
        indicator.innerHTML = '<div class="message-content">Typing...</div>';
        chatbotMessages.appendChild(indicator);
        chatbotMessages.scrollTop = chatbotMessages.scrollHeight;
    }

    function removeTypingIndicator() {
        const indicator = document.getElementById('typing-indicator');
        if (indicator) indicator.remove();
    }

    // Helper: Escape HTML
    function escapeHTML(str) {
        return str.replace(/[&<>"']/g, function (tag) {
            const chars = {
                '&': '&amp;',
                '<': '&lt;',
                '>': '&gt;',
                '"': '&quot;',
                "'": '&#39;'
            };
            return chars[tag] || tag;
        });
    }

    // Helper: Convert newlines to <br>
    function nl2br(str) {
        return str.replace(/\n/g, '<br>');
    }

    // Helper: Linkify
    function linkify(text) {
        const urlPattern = /(https?:\/\/[^\s]+)/g;
        return text.replace(urlPattern, url => `<a href="${url}" target="_blank" style="color: #4CC9F0; text-decoration: underline;">${url}</a>`);
    }

    // Starter Prompts
    // Starter Prompts
    const allPrompts = [

        /* ============================
           🔥 SECTION 1 — GENERAL PROFILE QUERIES (1–25)
           ============================ */
        "Pull up Samarth’s complete candidate profile from memory.",
        "Give me a recruiter-friendly overview of Samarth’s background.",
        "Summarize Samarth’s strongest technical skills.",
        "List Samarth’s top areas of backend expertise.",
        "What unique strengths differentiate Samarth from typical new-grad engineers?",
        "Summarize Samarth’s work experience in under 5 bullet points.",
        "What industries has Samarth worked in, and which ones does he prefer?",
        "Does Samarth demonstrate strong computer science fundamentals?",
        "What are Samarth’s soft skills based on his profile?",
        "Highlight three of Samarth’s most impactful achievements.",
        "Does Samarth have experience owning end-to-end projects?",
        "Has Samarth worked in high-growth or startup environments?",
        "List all major technologies Samarth has used in production.",
        "What recent frameworks, tools, or languages has Samarth adopted?",
        "Provide a concise narrative of Samarth’s engineering career.",
        "What motivates Samarth based on his projects and roles?",
        "What problem-solving strengths show up across Samarth’s work?",
        "Does Samarth’s project history suggest strong leadership potential?",
        "What long-term career goals does Samarth express?",
        "Which type of team structure does Samarth perform best in?",
        "Summarize Samarth’s profile from the perspective of a hiring manager.",
        "What evidence shows Samarth is self-driven and proactive?",
        "Explain Samarth’s overall engineering philosophy.",
        "What is Samarth’s preferred working style based on his experience?",
        "Highlight Samarth’s entrepreneurial or builder instincts.",


        /* ============================
           🥇 SECTION 2 — TOP AI LABS (Anthropic, OpenAI, xAI, DeepMind) (26–40)
           ============================ */
        "How well does Samarth fit a backend engineering role at Anthropic?",
        "Evaluate Samarth’s suitability for OpenAI’s Applied Engineering track.",
        "Assess Samarth’s alignment with DeepMind’s research engineering expectations.",
        "Would Samarth be competitive for infrastructure roles at xAI?",
        "How do Samarth’s projects match the bar at top-tier AI labs like Anthropic?",
        "Does Samarth demonstrate readiness to build safe, reliable AI-driven systems?",
        "Which of Samarth’s projects best illustrate suitability for frontier AI companies?",
        "Would Samarth succeed in an AI lab requiring high systems rigor?",
        "How does Samarth compare to typical candidates at OpenAI?",
        "Assess whether Samarth’s LLM-related projects are competitive for DeepMind.",
        "Would Samarth excel in a hybrid research/engineering role at Anthropic?",
        "Does Samarth’s systems knowledge meet the bar for xAI’s platform teams?",
        "Which strengths make Samarth a good fit for frontier AI companies?",
        "What gaps, if any, would Samarth need to fill to join an AI lab?",
        "Does Samarth’s blend of DS, ML, and systems work fit modern AI engineering?",


        /* ============================
           🥈 SECTION 3 — QUANT, HFT & TRADING FIRMS (HRT, Jane Street, Two Sigma…) (41–65)
           ============================ */
        "Evaluate whether Samarth fits software roles at Hudson River Trading.",
        "How competitive is Samarth for Jane Street engineering roles?",
        "Does Samarth demonstrate the rigor required for Two Sigma engineering?",
        "Assess Samarth’s suitability for Optiver’s automation and infra teams.",
        "Would Samarth fit Citadel Securities’ low-latency systems engineering?",
        "How well does Samarth match Jump Trading’s engineering expectations?",
        "Does Samarth have the reliability mindset required for HFT environments?",
        "Would Samarth fit the engineering culture at IMC Trading?",
        "How strong is Samarth for DRW’s real-time systems teams?",
        "Evaluate Samarth for Bridgewater’s systems-focused engineering roles.",
        "Does Samarth meet the bar for Millennium platform engineering?",
        "Which of Samarth’s projects signal readiness for quant-style engineering?",
        "Does Samarth demonstrate the ability to build stable, fault-tolerant systems?",
        "Would Samarth be competitive in high-pressure real-time environments?",
        "How well does Samarth reason about performance and latency?",
        "Does Samarth’s debugging approach match what quant firms expect?",
        "Which strengths make Samarth suitable for HFT or trading firms?",
        "Does Samarth’s academic foundation align with quant engineering expectations?",
        "How would Samarth perform in research-infra-heavy teams like Two Sigma?",
        "Would Samarth excel in financial systems engineering roles?",
        "Does Samarth show a quant-like attention to systems correctness?",
        "Evaluate Samarth’s concurrency experience for low-latency infra.",
        "How does Samarth’s experience map to reliability needs of trading systems?",
        "Could Samarth support mission-critical market-data pipelines?",
        "Would Samarth be capable of building robust distributed financial systems?",


        /* ============================
           🥉 SECTION 4 — FAANG++ (Google, Meta, Apple, Amazon, Microsoft…) (66–95)
           ============================ */
        "How well does Samarth fit Google’s backend SWE expectations?",
        "Evaluate Samarth for Meta’s production engineering teams.",
        "Is Samarth aligned with Apple’s high-quality systems engineering standards?",
        "Assess Samarth’s suitability for Amazon’s SDE II-level responsibilities.",
        "How competitive is Samarth for Microsoft’s cloud/backend roles?",
        "Evaluate Samarth’s fit for Netflix’s data platform teams.",
        "Would Samarth be a strong fit for Uber’s real-time systems teams?",
        "Assess Samarth’s readiness for Tesla Autopilot backend infra.",
        "Does Samarth have the systems depth needed at NVIDIA?",
        "How does Samarth compare to typical L4 Google SWE candidates?",
        "Evaluate Samarth’s ability to pass Meta’s HCs based on his profile.",
        "Would Samarth be a strong contributor at Amazon’s high-ownership teams?",
        "Does Samarth meet the quality bar for Apple platform engineering?",
        "Would Samarth thrive inside large, structured FAANG teams?",
        "How does Samarth’s coding style align with FAANG engineering culture?",
        "Which aspects of Samarth’s work show FAANG-level rigor?",
        "Does Samarth demonstrate scalability thinking expected at Google?",
        "How does Samarth handle ambiguity—important for Meta and Uber?",
        "Evaluate Samarth’s readiness for distributed systems design interviews.",
        "Would Samarth excel in Netflix’s freedom-and-responsibility model?",
        "Does Samarth show the craftsmanship Apple values in engineers?",
        "Rate Samarth’s overall FAANG+ competitiveness.",
        "Identify roles within FAANG+ where Samarth would be strongest.",
        "Which FAANG company aligns best with Samarth’s engineering style?",
        "Would Samarth succeed in highly cross-functional teams like Amazon?",


        /* ============================
           ⚙️ SECTION 5 — TOP INFRA / CLOUD / DEV-TOOLS (96–120)
           ============================ */
        "Evaluate Samarth’s fit for Databricks’ data infrastructure teams.",
        "Does Samarth match Snowflake’s expectations for systems engineers?",
        "How well does Samarth align with Stripe’s reliability-focused backend teams?",
        "Would Samarth fit Cloudflare’s globally distributed edge platform?",
        "Assess Samarth’s suitability for Confluent’s event-streaming platform teams.",
        "Evaluate Samarth’s experience for Palantir’s infrastructure roles.",
        "Does Samarth have the systems strength needed at HashiCorp?",
        "Would Samarth contribute effectively at MongoDB’s core server teams?",
        "Assess Samarth’s alignment with Reddit’s high-load platform engineering.",
        "Which infra companies are an ideal match for Samarth’s systems background?",
        "Does Samarth’s work demonstrate deep understanding of data systems?",
        "Would Samarth excel in dev-tools or infra-as-code environments?",
        "Evaluate Samarth’s backend depth for OpenSearch/Elasticsearch-type roles.",
        "Does Samarth have enough distributed systems exposure for cloud infra teams?",
        "How competitive is Samarth for global-scale infra companies?",
        "Assess Samarth’s readiness for Stripe-like transactional consistency models.",
        "Which infra problems is Samarth best suited to solve?",
        "Does Samarth’s experience reflect strong observability and monitoring practices?",
        "Would Samarth fit a resilience engineering team at a cloud provider?",
        "How does Samarth handle reliability trade-offs critical in infra companies?",
        "Which dev-tools companies match Samarth’s engineering preferences?",
        "Would Samarth excel in designing developer-focused backend systems?",
        "Does Samarth demonstrate readiness for data-lake or warehouse teams?",
        "Assess Samarth’s experience with indexing, storage, and latency-sensitive APIs.",
        "Identify where Samarth would thrive most in the cloud/infra ecosystem.",


        /* ============================
           🚀 SECTION 6 — STARTUPS & GROWTH COMPANIES (Notion, Figma…) (121–135)
           ============================ */
        "How well does Samarth fit engineering roles at high-growth startups?",
        "Evaluate Samarth’s suitability for Notion’s platform teams.",
        "Would Samarth excel at Figma’s collaboration and real-time infra teams?",
        "Assess Samarth’s alignment with Rippling’s automation/backend stack.",
        "Is Samarth a strong match for Samsara’s IoT and cloud infra teams?",
        "Does Samarth fit ServiceTitan’s enterprise SaaS engineering culture?",
        "Evaluate Samarth for Ramp’s financial platform engineering teams.",
        "Would Samarth be competitive at OpenSea’s marketplace infrastructure?",
        "How well does Samarth match Robinhood’s backend system needs?",
        "Evaluate Samarth’s alignment with Plaid’s API-first engineering model.",
        "Does Samarth have strengths matching Affirm’s payments backend?",
        "Would Samarth be a strong contributor at Coupang’s large-scale infra?",
        "Which startup engineering environments best align with Samarth’s strengths?",
        "Does Samarth fit early-stage engineering expectations?",
        "Would Samarth excel in high-autonomy product engineering roles?",


        /* ============================
           🌐 SECTION 7 — ENTERPRISE SAAS / CLOUD / ADTECH (135–145)
           ============================ */
        "Evaluate Samarth’s fit for The Trade Desk’s real-time ad systems.",
        "Does Samarth meet Broadcom’s standards for core systems work?",
        "Assess Samarth’s suitability for Workday’s enterprise backend teams.",
        "Would Samarth match Adobe’s platform engineering bar?",
        "Evaluate Samarth for Cisco’s cloud infrastructure roles.",
        "Does Samarth fit the expectations at Oracle for infrastructure engineering?",
        "Which enterprise SaaS roles are ideal for Samarth’s background?",
        "Does Samarth’s experience show strength in high-reliability enterprise systems?",
        "How competitive would Samarth be in ad-tech backend engineering?",
        "Which enterprise engineering environments match Samarth’s style?",


        /* ============================
           ⭐ SECTION 8 — BONUS COMPANIES (145–155)
           ============================ */
        "Evaluate Samarth’s fit for ByteDance/TikTok’s backend teams.",
        "Would Samarth excel in Waymo’s autonomous systems backend?",
        "Assess Samarth’s suitability for AMD’s systems engineering roles.",
        "Does Samarth match IBM Research’s experimental engineering profile?",
        "How competitive is Samarth for Capital One’s distinguished engineering track?",
        "Would Samarth thrive in Roblox’s massive-scale game-backend environment?",
        "Evaluate Samarth’s fit for StubHub’s API platform engineering.",
        "Does Samarth have the skills for MOLOCO’s ad-tech infra teams?",
        "Which bonus-company roles would Samarth match best?",
        "Compare Samarth’s strengths to expectations across all bonus companies.",


        /* ============================
           🎯 SECTION 9 — GENERAL JOB-FIT & INSIGHT PROMPTS (156–175)
           ============================ */
        "Which company category—AI labs, FAANG, quant, infra—best suits Samarth?",
        "How does Samarth compare to top 1% backend candidates?",
        "Which companies would value Samarth’s distributed systems experience most?",
        "Where would Samarth’s AI + backend combination be most impactful?",
        "Does Samarth demonstrate a staff-engineer trajectory long-term?",
        "Which teams would immediately benefit from Samarth’s project experience?",
        "What technical gaps should Samarth close for elite-tier companies?",
        "How does Samarth’s engineering maturity compare to senior-level candidates?",
        "Would Samarth excel more in structured environments or fast-growth startups?",
        "Which aspects of Samarth’s portfolio are most attractive to recruiters?"
    ];


    function refreshStarterPrompts() {
        if (!starterPromptsContainer) return;
        starterPromptsContainer.innerHTML = ''; // Clear existing

        // Shuffle and pick 3
        const shuffled = allPrompts.sort(() => 0.5 - Math.random());
        const selected = shuffled.slice(0, 3);

        selected.forEach(prompt => {
            const btn = document.createElement('button');
            btn.classList.add('chatbot-starter-prompt');
            btn.textContent = prompt;
            btn.addEventListener('click', () => {
                chatbotInput.value = prompt;
                sendMessage();
            });
            starterPromptsContainer.appendChild(btn);
        });

        // Ensure they are visible
        starterPromptsContainer.style.display = 'flex';
    }

    // Initial Load
    refreshStarterPrompts();

    // Refresh on Toggle Open
    chatbotToggle.addEventListener('click', () => {
        refreshStarterPrompts();
        chatbotContainer.classList.add('active');
        chatbotToggle.style.display = 'none';
        chatbotInput.focus();
    });

    // Refresh on Close
    chatbotClose.addEventListener('click', () => {
        refreshStarterPrompts();
        chatbotContainer.classList.remove('active');
        chatbotToggle.style.display = 'flex';
    });

    // --- Custom Cursor Logic-- -
    const cursorDot = document.querySelector('.cursor-dot');
    const cursorOutline = document.querySelector('.cursor-outline');

    if (cursorDot && cursorOutline) {
        window.addEventListener('mousemove', function (e) {
            const posX = e.clientX;
            const posY = e.clientY;

            // Dot follows instantly
            cursorDot.style.left = `${posX}px`;
            cursorDot.style.top = `${posY}px`;

            // Outline follows with slight delay (using animate for smoothness)
            cursorOutline.animate({
                left: `${posX}px`,
                top: `${posY}px`
            }, { duration: 500, fill: "forwards" });
        });

        // Hover effects
        const hoverables = document.querySelectorAll('a, button, .project-card, .bento-item');
        hoverables.forEach(el => {
            el.addEventListener('mouseenter', () => document.body.classList.add('hovering'));
            el.addEventListener('mouseleave', () => document.body.classList.remove('hovering'));
        });
    }

    // --- Floating Logos (TrueUp Style) ---
    function initFloatingLogos() {
        const container = document.getElementById('floating-logos');
        if (!container) return;

        const logos = [
            'python', 'openjdk', 'cplusplus', 'rust', 'typescript', 'django', 
            'nodedotjs', 'fastapi', 'redis', 'apachekafka', 'postgresql', 
            'mongodb', 'elasticsearch', 'openai', 'amazonwebservices', 
            'docker', 'kubernetes', 'react', 'nextdotjs', 'tailwind-css',
            'googlegpu', 'pytorch', 'tensorflow', 'scikitlearn', 'pandas', 'numpy',
            'celery', 'graphql', 'pytest', 'junit5', 'prometheus', 'puppeteer', 
            'twilio', 'terraform', 'githubactions', 'opentelemetry', 'istio', 'jenkins'
        ];

        // Shuffle logos to pick unique ones
        const shuffledLogos = logos.sort(() => 0.5 - Math.random());
        // Pick fewer logos on mobile for performance
        const isMobile = window.innerWidth < 768;
        const logoLimit = isMobile ? 15 : 30;
        const logoCount = Math.min(shuffledLogos.length, logoLimit);

        for (let i = 0; i < logoCount; i++) {
            const logo = shuffledLogos[i];
            const el = document.createElement('img');
            el.src = `https://cdn.simpleicons.org/${logo}`;
            el.loading = 'lazy';
            el.className = 'floating-logo-item';
            
            // Random position
            const x = Math.random() * 100;
            const y = Math.random() * 100;
            const size = 45; // 45px uniform size
            
            el.style.left = `${x}%`;
            el.style.top = `${y}%`;
            el.style.width = `${size}px`;
            el.style.height = `${size}px`;
            
            // Increased drift variables for better movement
            const driftX = (Math.random() - 0.5) * 400; // Even more range
            const driftY = (Math.random() - 0.5) * 400; // Even more range
            const driftRotate = (Math.random() - 0.5) * 360; // Potential full spin
            const duration = 8 + Math.random() * 12; // Much faster (8s to 20s)
            const delay = -Math.random() * duration;
            
            el.style.setProperty('--drift-x', `${driftX}px`);
            el.style.setProperty('--drift-y', `${driftY}px`);
            el.style.setProperty('--drift-rotate', `${driftRotate}deg`);
            el.style.animationDuration = `${duration}s`;
            el.style.animationDelay = `${delay}s`;
            
            container.appendChild(el);
        }
    }

    initFloatingLogos();

    // --- Magnetic Buttons - ENHANCED ---
    const magneticElements = document.querySelectorAll('.magnetic-btn, .magnetic-link, .btn');
    magneticElements.forEach(el => {
        el.addEventListener('mousemove', function (e) {
            const rect = el.getBoundingClientRect();
            const x = e.clientX - rect.left - rect.width / 2;
            const y = e.clientY - rect.top - rect.height / 2;

            // Enhanced magnetic strength (0.3 → 0.6)
            el.style.transform = `translate(${x * 0.6}px, ${y * 0.6}px) scale(1.05)`;
        });

        el.addEventListener('mouseleave', function () {
            el.style.transform = 'translate(0, 0) scale(1)';
            el.style.transition = 'transform 0.4s cubic-bezier(0.34, 1.56, 0.64, 1)';
        });

        el.addEventListener('mouseenter', function () {
            el.style.transition = 'transform 0.1s ease-out';
        });
    });

    // --- Scroll Progress Bar ---
    window.addEventListener('scroll', () => {
        const scrollTop = document.documentElement.scrollTop || document.body.scrollTop;
        const scrollHeight = document.documentElement.scrollHeight - document.documentElement.clientHeight;
        const scrolled = (scrollTop / scrollHeight) * 100;

        const progressBar = document.getElementById('scroll-progress');
        if (progressBar) {
            progressBar.style.width = `${scrolled}%`;
        }

        // Sticky Navbar Toggle
        const navbar = document.querySelector('.navbar');
        if (navbar) {
            if (scrollTop > 50) {
                navbar.classList.add('scrolled');
            } else {
                navbar.classList.remove('scrolled');
            }
        }
    });

    // Particles disabled for clean white theme
    // (#particles-js is hidden via CSS display:none)

    // --- LeetCode Stats ---
    async function fetchLeetCodeStats() {
        console.log('Fetching LeetCode stats...');
        try {
            const query = `
                query userProfileUserQuestionProgressV2($userSlug: String!) {
                    userProfileUserQuestionProgressV2(userSlug: $userSlug) {
                        numAcceptedQuestions {
                            count
                            difficulty
                        }
                        numFailedQuestions {
                            count
                            difficulty
                        }
                        numUntouchedQuestions {
                            count
                            difficulty
                        }
                        userSessionBeatsPercentage {
                            difficulty
                            percentage
                        }
                        totalQuestionBeatsPercentage
                    }
                }
            `;

            const variables = {
                userSlug: "samarthmahendra"
            };
            const SERVER_URL = 'https://samarthmahendra-github-io.onrender.com';
            // const SERVER_URL = 'http://0.0.0.0:8000';
            const response = await fetch(`${SERVER_URL}/leetcode/proxy`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    query: query,
                    variables: variables,
                    operationName: "userProfileUserQuestionProgressV2"
                })
            });

            const data = await response.json();

            if (data.data && data.data.userProfileUserQuestionProgressV2) {
                const progress = data.data.userProfileUserQuestionProgressV2;

                // Extract counts by difficulty
                const acceptedQuestions = progress.numAcceptedQuestions;
                let easyCount = 0, mediumCount = 0, hardCount = 0, totalCount = 0;

                acceptedQuestions.forEach(item => {
                    if (item.difficulty === 'EASY') easyCount = item.count;
                    else if (item.difficulty === 'MEDIUM') mediumCount = item.count;
                    else if (item.difficulty === 'HARD') hardCount = item.count;
                });

                totalCount = easyCount + mediumCount + hardCount;

                // Set initial to 0, store target, and animate immediately
                const lcTotal = document.getElementById('lc-total');
                const lcEasy = document.getElementById('lc-easy');
                const lcMedium = document.getElementById('lc-medium');
                const lcHard = document.getElementById('lc-hard');

                if (lcTotal) {
                    lcTotal.textContent = '0';
                    lcTotal.setAttribute('data-target', totalCount);
                    animateCounter(lcTotal, totalCount, 1500);
                    animatedCounters.add('lc-total'); // Mark as animated
                }
                if (lcEasy) {
                    lcEasy.textContent = '0';
                    lcEasy.setAttribute('data-target', easyCount);
                    animateCounter(lcEasy, easyCount, 1500);
                    animatedCounters.add('lc-easy'); // Mark as animated
                }
                if (lcMedium) {
                    lcMedium.textContent = '0';
                    lcMedium.setAttribute('data-target', mediumCount);
                    animateCounter(lcMedium, mediumCount, 1500);
                    animatedCounters.add('lc-medium'); // Mark as animated
                }
                if (lcHard) {
                    lcHard.textContent = '0';
                    lcHard.setAttribute('data-target', hardCount);
                    animateCounter(lcHard, hardCount, 1500);
                    animatedCounters.add('lc-hard'); // Mark as animated
                }

                console.log('LeetCode stats updated:', {
                    total: totalCount,
                    easy: easyCount,
                    medium: mediumCount,
                    hard: hardCount
                });
            }
        } catch (error) {
            console.error('Error fetching LeetCode stats:', error);
            // Fallback to known values
            document.getElementById('lc-total').textContent = '397';
            document.getElementById('lc-easy').textContent = '163';
            document.getElementById('lc-medium').textContent = '194';
            document.getElementById('lc-hard').textContent = '40';
        }
    }



    // --- LeetCode Calendar Stats (2025 Active Days & Streak) ---
    async function fetchLeetCodeCalendarStats() {
        console.log('Fetching LeetCode calendar stats...');
        try {
            const query = `
                query userProfileCalendar($username: String!, $year: Int) {
                    matchedUser(username: $username) {
                        userCalendar(year: $year) {
                            activeYears
                            streak
                            totalActiveDays
                            submissionCalendar
                        }
                    }
                }
            `;

            const variables = {
                username: "samarthmahendra",
            };
            const SERVER_URL = 'https://samarthmahendra-github-io.onrender.com';
            // const SERVER_URL = 'http://0.0.0.0:8000';

            const response = await fetch(`${SERVER_URL}/leetcode/proxy`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    query: query,
                    variables: variables,
                    operationName: "userProfileCalendar"
                })
            });

            const data = await response.json();

            if (data.data && data.data.matchedUser && data.data.matchedUser.userCalendar) {
                const calendar = data.data.matchedUser.userCalendar;

                // Update active days and streak
                document.getElementById('lc-active-days').textContent = calendar.totalActiveDays || '--';
                document.getElementById('lc-streak').textContent = calendar.streak || '--';

                console.log('Calendar stats updated:', {
                    activeDays: calendar.totalActiveDays,
                    streak: calendar.streak
                });
            }
        } catch (error) {
            console.error('Error fetching LeetCode calendar stats:', error);
            // Fallback to known values
            document.getElementById('lc-active-days').textContent = '156';
            document.getElementById('lc-streak').textContent = '17';
        }
    }

    // --- GitHub Stats ---
    async function fetchGitHubStats() {
        const usernames = ['SamarthMahendraneu', 'SamarthMahendra-Draup'];

        try {
            let totalRepos = 0;
            let totalContributionsAllTime = 0;
            let lastYearContributions = 0;
            let past5YearsContributions = 0;
            const allLanguages = {};

            const currentYear = new Date().getFullYear();
            const lastYear = currentYear; // 2024
            const past5YearsStart = currentYear - 5; // 2020

            // Fetch data for both usernames
            for (const username of usernames) {
                try {
                    // Fetch user data
                    const userResponse = await fetch(`https://api.github.com/users/${username}`);
                    const userData = await userResponse.json();

                    // Add to total repos
                    totalRepos += userData.public_repos || 0;

                    // Fetch all repos to get languages
                    const reposResponse = await fetch(`https://api.github.com/users/${username}/repos?per_page=100`);
                    const repos = await reposResponse.json();

                    // Aggregate languages
                    repos.forEach(repo => {
                        if (repo.language) {
                            allLanguages[repo.language] = (allLanguages[repo.language] || 0) + 1;
                        }
                    });

                    // Fetch contributions for all available years
                    // We'll fetch from 2018 to current year to get all contributions
                    for (let year = 2018; year <= currentYear; year++) {
                        try {
                            const contributionsResponse = await fetch(
                                `https://github-contributions-api.jogruber.de/v4/${username}?y=${year}`
                            );
                            const contributionsData = await contributionsResponse.json();
                            const yearContributions = contributionsData.total?.[year] || 0;

                            // Add to total contributions
                            totalContributionsAllTime += yearContributions;

                            // Add to last year if it's 2024
                            if (year === lastYear) {
                                lastYearContributions += yearContributions;
                            }

                            // Add to past 5 years if within range (2020-2024)
                            if (year >= past5YearsStart && year <= currentYear) {
                                past5YearsContributions += yearContributions;
                            }
                        } catch (yearError) {
                            console.warn(`Could not fetch contributions for ${username} in ${year}:`, yearError);
                        }
                    }
                } catch (userError) {
                    console.warn(`Could not fetch data for ${username}:`, userError);
                }
            }

            // Helper: format number as shorthand (2537 → "2.5k+")
            function formatShortK(n) {
                if (n >= 1000) {
                    const k = n / 1000;
                    return (Number.isInteger(k) ? k : Math.floor(k * 10) / 10) + 'k+';
                }
                return String(n);
            }

            const ghTotalContributions = document.getElementById('gh-total-contributions');
            const ghRepos = document.getElementById('gh-repos');
            const ghLastYear = document.getElementById('gh-last-year');
            const ghPast5Years = document.getElementById('gh-past-5-years');

            if (ghTotalContributions) {
                ghTotalContributions.textContent = '0';
                // Animate counter, then snap to shorthand at the end
                animateCounter(ghTotalContributions, totalContributionsAllTime, 1500);
                setTimeout(() => {
                    ghTotalContributions.textContent = formatShortK(totalContributionsAllTime);
                }, 1600);
                animatedCounters.add('gh-total-contributions');
            }
            if (ghRepos) {
                ghRepos.textContent = '0';
                ghRepos.setAttribute('data-target', totalRepos);
                animateCounter(ghRepos, totalRepos, 1500);
                animatedCounters.add('gh-repos');
            }

            if (ghLastYear) {
                ghLastYear.textContent = '0';
                ghLastYear.setAttribute('data-target', lastYearContributions);
                animateCounter(ghLastYear, lastYearContributions, 1500);
                animatedCounters.add('gh-last-year');
            }
            if (ghPast5Years) {
                ghPast5Years.textContent = '0';
                ghPast5Years.setAttribute('data-target', past5YearsContributions);
                animateCounter(ghPast5Years, past5YearsContributions, 1500);
                animatedCounters.add('gh-past-5-years');
            }

            // Show top 3 languages as clean text
            const topLanguages = ['Python', 'Java', 'C++', 'JavaScript', 'TypeScript'];
            const top3 = topLanguages.slice(0, 3);

            // Display top languages as plain text (not pills) to avoid crowding
            const langTagsContainer = document.getElementById('gh-lang-tags');
            if (langTagsContainer) langTagsContainer.textContent = top3.join(', ');

            console.log('GitHub stats updated:', {
                totalContributions: totalContributionsAllTime,
                repos: totalRepos,
                lastYear: lastYearContributions,
                past5Years: past5YearsContributions
            });

        } catch (error) {
            console.error('Error fetching GitHub stats:', error);
            document.getElementById('gh-total-contributions').textContent = '5000+';
            document.getElementById('gh-repos').textContent = '80+';
            document.getElementById('gh-last-year').textContent = '800+';
            document.getElementById('gh-past-5-years').textContent = '4000+';
            if (document.getElementById('gh-lang-tags'))
                document.getElementById('gh-lang-tags').textContent = 'Python, Java, C++';

        }
    }

    fetchGitHubStats();

    // --- 3D Tilt Effect & Glare ---
    // Applying to Bento items, Project cards, and Timeline content
    const cards = document.querySelectorAll('.bento-item, .project-card, .timeline-content');

    cards.forEach(card => {
        // Create Glare Element
        const glare = document.createElement('div');
        glare.classList.add('glare');
        card.appendChild(glare);

        card.addEventListener('mousemove', (e) => {
            // Don't apply tilt if hovering over a link or image
            if (e.target.tagName === 'A' || e.target.tagName === 'IMG' || e.target.closest('a')) {
                return;
            }

            const rect = card.getBoundingClientRect();
            const x = e.clientX - rect.left;
            const y = e.clientY - rect.top;

            const centerX = rect.width / 2;
            const centerY = rect.height / 2;

            // Reduced intensity for timeline items (3 degrees instead of 12)
            // Keep higher intensity for bento and project cards
            let intensity = 12;
            if (card.classList.contains('timeline-content')) {
                intensity = 3; // Much less dramatic for experience section
            }

            const rotateX = ((y - centerY) / centerY) * -intensity;
            const rotateY = ((x - centerX) / centerX) * intensity;

            card.style.transform = `perspective(800px) rotateX(${rotateX}deg) rotateY(${rotateY}deg) scale(1.02)`;

            // Glare Position
            glare.style.left = `${x - 100}px`; // Center glare (200px width / 2)
            glare.style.top = `${y - 100}px`;
            glare.style.opacity = '1';
        });

        card.addEventListener('mouseleave', () => {
            card.style.transform = 'perspective(800px) rotateX(0) rotateY(0) scale(1)';
            glare.style.opacity = '0';
        });
    });

    fetchLeetCodeStats();
    fetchLeetCodeCalendarStats();

    // --- Profile Image Hover Effect ---
    const photoCard = document.querySelector('.photo-card');
    if (photoCard) {
        const profileImg = photoCard.querySelector('.profile-img');
        if (profileImg) {
            // Store the original source
            const originalSrc = profileImg.getAttribute('src'); // Get the attribute value directly
            const hoverSrc = 'display_picturev2.png';

            console.log('Hover effect initialized for photo-card.');
            console.log('Original Image Source:', originalSrc);
            console.log('Hover Image Source:', hoverSrc);

            // Preload the hover image
            const preloadImg = new Image();
            preloadImg.src = hoverSrc;

            photoCard.addEventListener('mouseenter', () => {
                console.log('Mouse enter photo-card: switching to', hoverSrc);
                profileImg.setAttribute('src', hoverSrc);
            });

            photoCard.addEventListener('mouseleave', () => {
                console.log('Mouse leave photo-card: switching to', originalSrc);
                profileImg.setAttribute('src', originalSrc);
            });
        }
    }
    // --- End of Profile Image Hover Effect ---

    // --- Spotlight Effect ---
    const spotlightCards = document.querySelectorAll('.bento-item');
    spotlightCards.forEach(card => {
        card.addEventListener('mousemove', (e) => {
            const rect = card.getBoundingClientRect();
            const x = e.clientX - rect.left;
            const y = e.clientY - rect.top;

            card.style.setProperty('--mouse-x', `${x}px`);
            card.style.setProperty('--mouse-y', `${y}px`);
        });
    });

    // --- 3D Stacking Scroll Effect for Experience Cards ---
    function update3DStackEffect() {
        const timelineItems = document.querySelectorAll('.timeline-item');
        const stickyTop = 100; // Match the CSS sticky top value

        timelineItems.forEach((item, index) => {
            const rect = item.getBoundingClientRect();
            const itemTop = rect.top;
            const itemHeight = rect.height;

            // Calculate how far this item has scrolled past its sticky position
            const scrollProgress = Math.max(0, Math.min(1, (stickyTop - itemTop) / (itemHeight * 0.5)));

            // Check if there's a card below this one that's approaching
            let pushBackProgress = 0;
            if (index < timelineItems.length - 1) {
                const nextItem = timelineItems[index + 1];
                const nextRect = nextItem.getBoundingClientRect();
                const nextTop = nextRect.top;

                // Calculate how close the next card is to pushing this one back
                // When next card is within viewport height of sticky position, start pushing
                const pushDistance = window.innerHeight * 0.6;
                const distanceToSticky = nextTop - stickyTop;
                pushBackProgress = Math.max(0, Math.min(1, 1 - (distanceToSticky / pushDistance)));
            }

            // Calculate scale and translateZ based on scroll progress
            // Each subsequent card should be slightly smaller and further back
            const baseScale = 1 - (index * 0.02); // Each card starts slightly smaller
            const scrollScale = scrollProgress * 0.15; // Scale down as it scrolls past
            const pushBackScale = pushBackProgress * 0.12; // Scale down when pushed by next card
            const scale = baseScale - scrollScale - pushBackScale;

            const scrollTranslateZ = -(scrollProgress * 100); // Move back when scrolled past
            const pushBackTranslateZ = -(pushBackProgress * 80); // Move back when pushed
            const baseTranslateZ = -(index * 20); // Base depth for stacking
            const translateZ = scrollTranslateZ + pushBackTranslateZ + baseTranslateZ;

            const opacity = 1 - (scrollProgress * 0.3) - (pushBackProgress * 0.2); // Fade on both

            // Calculate blur - increases as card goes back in 3D space
            const scrollBlur = scrollProgress * 8; // Max 8px blur when scrolled past
            const pushBackBlur = pushBackProgress * 5; // Max 5px blur when pushed back
            const blurAmount = scrollBlur + pushBackBlur;

            // Apply transforms
            const content = item.querySelector('.timeline-content');
            const dot = item.querySelector('.timeline-dot');

            if (content) {
                content.style.transform = `
                    scale(${Math.max(0.8, scale)}) 
                    translateZ(${translateZ}px)
                    rotateX(${(scrollProgress + pushBackProgress * 0.5) * 2}deg)
                `;
                content.style.opacity = Math.max(0.6, opacity);
                content.style.filter = `brightness(${1 - (scrollProgress + pushBackProgress) * 0.2}) blur(${blurAmount}px)`;
            }

            if (dot) {
                dot.style.transform = `scale(${1 - (scrollProgress + pushBackProgress * 0.5) * 0.3})`;
                dot.style.opacity = Math.max(0.5, 1 - (scrollProgress + pushBackProgress * 0.5) * 0.5);
            }
        });
    }

    // Run on scroll
    window.addEventListener('scroll', update3DStackEffect);
    // Run on load
    update3DStackEffect();

    // --- Premium Scroll Effects ---

    // 1. Navbar Shrink on Scroll
    const navbar = document.querySelector('.navbar');
    let lastScrollTop = 0;

    function updateNavbar() {
        const scrollTop = window.pageYOffset || document.documentElement.scrollTop;

        if (scrollTop > 100) {
            navbar.classList.add('scrolled');
        } else {
            navbar.classList.remove('scrolled');
        }

        lastScrollTop = scrollTop;
    }

    // 2. Parallax Background Effect - DISABLED per user preference
    // const bgGradient = document.querySelector('.bg-gradient');
    // const bgGrid = document.querySelector('.bg-grid');

    // function updateParallax() {
    //     const scrolled = window.pageYOffset;
    //     const parallaxSpeed = 0.5;
    //     
    //     if (bgGradient) {
    //         bgGradient.style.transform = `translateY(${scrolled * parallaxSpeed}px)`;
    //     }
    //     if (bgGrid) {
    //         bgGrid.style.transform = `translateY(${scrolled * parallaxSpeed * 0.3}px)`;
    //     }
    // }

    // Helper function to check if element is in viewport
    function isElementInView(element) {
        const rect = element.getBoundingClientRect();
        return rect.top < window.innerHeight * 0.8 && rect.bottom > 0;
    }

    // 3. Scroll Reveal Animations
    function revealOnScroll() {
        const reveals = document.querySelectorAll('.scroll-reveal');

        reveals.forEach(element => {
            const elementTop = element.getBoundingClientRect().top;
            const elementBottom = element.getBoundingClientRect().bottom;
            const windowHeight = window.innerHeight;

            // Reveal when element is 20% into viewport
            if (elementTop < windowHeight * 0.8 && elementBottom > 0) {
                element.classList.add('revealed');
            }
        });
    }

    // 4. Counter Animations for Stats
    function animateCounter(element, target, duration = 2000) {
        console.log('animateCounter called:', element.id, 'target:', target);
        const start = 0;
        const increment = target / (duration / 16); // 60fps
        let current = start;

        element.classList.add('counting');

        const timer = setInterval(() => {
            current += increment;
            if (current >= target) {
                element.textContent = target;
                console.log('Counter animation complete:', element.id, 'final value:', target);
                clearInterval(timer);
                element.classList.remove('counting');
            } else {
                element.textContent = Math.floor(current);
            }
        }, 16);
    }

    // Track which counters have been animated
    const animatedCounters = new Set();

    function checkCounters() {
        const counterElements = [
            { id: 'gh-total-contributions', animated: false },
            { id: 'gh-repos', animated: false },
            { id: 'gh-last-year', animated: false },
            { id: 'gh-past-5-years', animated: false },
            { id: 'lc-total', animated: false },
            { id: 'lc-easy', animated: false },
            { id: 'lc-medium', animated: false },
            { id: 'lc-hard', animated: false }
        ];

        counterElements.forEach(counter => {
            const element = document.getElementById(counter.id);
            if (element && !animatedCounters.has(counter.id)) {
                const rect = element.getBoundingClientRect();
                const inView = rect.top < window.innerHeight * 0.8 && rect.bottom > 0;

                if (inView) {
                    // Get target from data attribute
                    const target = parseInt(element.getAttribute('data-target'));
                    console.log('checkCounters:', counter.id, 'data-target:', element.getAttribute('data-target'), 'parsed:', target, 'inView:', inView);
                    if (!isNaN(target) && target > 0) {
                        animateCounter(element, target, 1500);
                        animatedCounters.add(counter.id);
                    }
                }
            }
        });
    }

    // 5. Add scroll-reveal class to project cards
    function initScrollReveal() {
        const projectCards = document.querySelectorAll('.project-card');
        projectCards.forEach((card, index) => {
            card.classList.add('scroll-reveal');
            if (index < 6) {
                card.classList.add(`delay-${index + 1}`);
            }
        });

        // Initial check
        revealOnScroll();
    }

    // Optimized scroll handler using requestAnimationFrame
    let ticking = false;

    function onScroll() {
        if (!ticking) {
            window.requestAnimationFrame(() => {
                updateNavbar();
                // updateParallax(); // Disabled per user preference
                revealOnScroll();
                checkCounters();
                ticking = false;
            });
            ticking = true;
        }
    }

    // Initialize
    initScrollReveal();
    window.addEventListener('scroll', onScroll);

    // Run once on load
    updateNavbar();
    // updateParallax(); // Disabled per user preference
    revealOnScroll();

    // --- Smooth Anchor Scrolling ---
    document.querySelectorAll('a[href^="#"]').forEach(anchor => {
        anchor.addEventListener('click', function (e) {
            const href = this.getAttribute('href');
            if (href === '#') return;

            e.preventDefault();
            const target = document.querySelector(href);
            if (target) {
                const navbarHeight = 80;
                const targetPosition = target.getBoundingClientRect().top + window.pageYOffset - navbarHeight;

                window.scrollTo({
                    top: targetPosition,
                    behavior: 'smooth'
                });

                history.pushState(null, null, href);
            }
        });
    });

    // --- Cursor Trail Effect ---
    let particles = [];
    const maxParticles = 20;
    const colors = ['#8A42D1', '#6B5FCD', '#4CC9F0', '#5BBEF0'];

    if (!('ontouchstart' in window)) {
        document.addEventListener('mousemove', (e) => {
            const particle = document.createElement('div');
            particle.className = 'cursor-trail';

            const color = colors[Math.floor(Math.random() * colors.length)];
            particle.style.background = color;
            particle.style.left = e.clientX + 'px';
            particle.style.top = e.clientY + 'px';

            document.body.appendChild(particle);
            particles.push(particle);

            setTimeout(() => {
                particle.remove();
                particles.shift();
            }, 600);

            if (particles.length > maxParticles) {
                const oldParticle = particles.shift();
                oldParticle.remove();
            }
        });
    }

    // --- Text Scramble Animation ---
    class TextScramble {
        constructor(el) {
            this.el = el;
            this.chars = '!<>-_\\/[]{}—=+*^?#________';
            this.update = this.update.bind(this);
        }

        setText(newText) {
            const oldText = this.el.innerText;
            const length = Math.max(oldText.length, newText.length);
            const promise = new Promise((resolve) => this.resolve = resolve);
            this.queue = [];

            for (let i = 0; i < length; i++) {
                const from = oldText[i] || '';
                const to = newText[i] || '';
                const start = Math.floor(Math.random() * 40);
                const end = start + Math.floor(Math.random() * 40);
                this.queue.push({ from, to, start, end });
            }

            cancelAnimationFrame(this.frameRequest);
            this.frame = 0;
            this.update();
            return promise;
        }

        update() {
            let output = '';
            let complete = 0;

            for (let i = 0, n = this.queue.length; i < n; i++) {
                let { from, to, start, end, char } = this.queue[i];

                if (this.frame >= end) {
                    complete++;
                    output += to;
                } else if (this.frame >= start) {
                    if (!char || Math.random() < 0.28) {
                        char = this.randomChar();
                        this.queue[i].char = char;
                    }
                    output += char;
                } else {
                    output += from;
                }
            }

            this.el.innerHTML = output;

            if (complete === this.queue.length) {
                this.resolve();
            } else {
                this.frameRequest = requestAnimationFrame(this.update);
                this.frame++;
            }
        }

        randomChar() {
            return this.chars[Math.floor(Math.random() * this.chars.length)];
        }
    }

    const sectionTitles = document.querySelectorAll('.section-title');
    const scrambledTitles = new Set();

    function scrambleTitles() {
        sectionTitles.forEach(title => {
            if (scrambledTitles.has(title)) return;

            const rect = title.getBoundingClientRect();
            if (rect.top < window.innerHeight * 0.8 && rect.bottom > 0) {
                const fx = new TextScramble(title);
                const originalText = title.textContent;
                fx.setText(originalText);
                scrambledTitles.add(title);
            }
        });
    }

    window.addEventListener('scroll', scrambleTitles);
    scrambleTitles();
});
