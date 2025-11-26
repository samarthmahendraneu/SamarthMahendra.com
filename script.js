document.addEventListener('DOMContentLoaded', function () {
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
        "Which of Samarth's projects best showcase his AI and data engineering expertise?",
        "Could you pull up Samarth’s profile and highlight his main qualifications?",
        "How well does Samarth's profile match this job description?",
        "How well does Samarth fit the Software Development Engineer role at Google based on his profile?",
        "Can you evaluate Samarth’s suitability for an SDE position at Amazon?",
        "Assess how Samarth’s experience aligns with Microsoft's Software Engineer role.",
        "Determine Samarth’s fit for Apple's Machine Learning Engineer position.",
        "Review Samarth’s skills and tell me how he matches a data engineer role at Netflix.",
        "Could you pull up Samarth’s full candidate profile from the database?",
        "What are Samarth’s top three technical skills listed in his profile?",
        "Give me an overview of his most recent work experience.",
        "What degrees and certifications does Samarth hold?",
        "Retrieve Samarth’s preferred contact email and phone number.",
        "Can you tell me Samarth’s strengths and soft skills from his profile?",
        "Ask Samarth on Discord if he’s available for a quick call tomorrow.",
        "Is Samarth free next Tuesday at 2 PM? If so, schedule a Jitsi meeting.",
        "Set up a 30-minute Jitsi call with Samarth and me this Friday at 10 AM.",
        "Schedule a team meeting with Samarth and our HR lead on June 5th at 3 PM.",
        "What major achievements are highlighted in Samarth’s profile?",
        "Gather job-fit insights for a DevOps role from his candidate profile.",
        "Can you confirm Samarth’s availability for a follow-up discussion next week?",
        "What recent technologies or frameworks has Samarth worked with?",
        "Has Samarth demonstrated leadership or mentorship in past roles?",
        "Can you generate 3 bullet points on why Samarth would be a strong hire?",
        "Compare Samarth’s resume with the job description for a backend engineer at Meta.",
        "What types of problems has Samarth solved in previous projects?",
        "Is there evidence that Samarth works well in distributed or remote teams?",
        "Show examples of Samarth collaborating cross-functionally in his past projects.",
        "What was the impact of Samarth’s most recent project on his team or company?",
        "Does Samarth’s experience suggest he can own end-to-end delivery of features?",
        "Highlight examples where Samarth improved system performance or efficiency.",
        "List 3 situations where Samarth demonstrated creative problem-solving.",
        "What cloud platforms is Samarth experienced with, and to what extent?",
        "Identify any DevOps, CI/CD, or observability tooling Samarth has used.",
        "Which of Samarth’s projects show production-grade engineering skills?",
        "Generate a summary of Samarth's AI voice assistant project for a hiring manager.",
        "Is Samarth open to relocation or remote-first roles?",
        "Check if Samarth has experience working in startup or high-growth environments.",
        "What industries has Samarth worked in, and which ones is he targeting?",
        "Does Samarth's profile show adaptability to fast-changing tech stacks?",
        "Evaluate whether Samarth’s skills align with the company's current tech roadmap.",
        "Can you suggest questions I should ask Samarth based on his profile?",
        "What sets Samarth apart from other candidates with similar experience?",
        "How recently has Samarth updated his resume or LinkedIn profile?",
        "Has Samarth received any awards, honors, or special recognition?",
        "Create a quick one-slide summary of Samarth for the hiring committee.",
        "What are Samarth’s long-term career goals based on his stated interests?",
        "Does Samarth demonstrate readiness for designing backend systems at scale for FAANG?",
        "What’s Samarth’s fluency with CI/CD, observability (e.g., Datadog, Prometheus), and infra-as-code?",
        "Assess how Samarth balances speed, resilience, and observability in real-time system architectures.",
        "How has Samarth collaborated with cross-functional teams to ship production features?",
        "Give examples of product-minded thinking in Samarth’s contributions to his projects?",
        "Which of Samarth’s projects show self-initiative and curiosity in unstructured environments?",
        "Evaluate how Samarth balances shipping velocity with learning advanced topics like distributed DBs.",
        "How does Samarth’s voice AI assistant illustrate his ability to combine research, product, and engineering?",
        "Does Samarth’s resume suggest trajectory toward tech leadership or staff+ engineering?",
        "What leadership traits are evident from Samarth’s backend lead role and hackathon wins?",
        "Could Samarth lead a small AI/infra team for an early-stage FAANG initiative?",
        "How does Samarth’s experience with streaming WebSockets and real-time voice AI demonstrate backend depth?",
        "Evaluate Samarth’s contributions in distributed systems and scalability from his candidate profile?",
        "Which of Samarth’s projects showcase mastery in LLM integration with efficient backend orchestration?",
        "How well does Samarth’s backend experience at Draup translate to Stripe’s payment processing and real-time data workflows?",
        "Evaluate whether Samarth’s work on dynamic query generation and Elasticsearch aligns with Stripe’s data pipeline design principles.",
        "How does Samarth’s understanding of distributed systems fit Stripe’s real-time transaction reconciliation systems?",
        "Which of Samarth’s projects best demonstrate his ability to build idempotent, high-availability APIs similar to Stripe’s architecture?",
        "Assess Samarth’s experience with Celery and Redis in terms of readiness for Stripe’s asynchronous task orchestration systems.",
        "Has Samarth built systems that ensure consistency and reliability under high load, similar to Stripe’s financial ledgers?",
        "Can Samarth’s background in building subscription access control map to Stripe’s billing and invoicing platform?",
        "Compare Samarth’s query optimization and API migration experience to the scale of Stripe’s financial data services.",
        "Would Samarth’s observability work with Datadog and Prometheus strengthen Stripe’s production monitoring reliability?",
        "Based on Samarth’s experience, how would he debug issues in a multi-service Stripe-like architecture with Kafka and Redis?",
        "How would Samarth approach designing a refund and dispute workflow like Stripe’s, based on his Draup background?",
        "Evaluate Samarth’s readiness to handle reconciliation tasks similar to Stripe’s clearing file ingestion and settlement pipelines.",
        "Has Samarth demonstrated the ability to think in terms of financial correctness, auditability, and idempotency in his past roles?",
        "What aspects of Samarth’s design patterns (Factory, Strategy, Validator) would be valuable in Stripe’s modular service architecture?",
        "Assess how Samarth’s experience in LLM integrations could help automate internal Stripe tooling for ops or reconciliation.",
        "How might Samarth apply his skills to Stripe’s data warehouse or transaction analytics layers?",
        "Could Samarth design a microservice to handle merchant payouts with retries, timeouts, and reconciliation safety?",
        "Does Samarth’s work show he can meet Stripe’s reliability bar for financial systems (five-nines availability)?",
        "How does Samarth’s system design knowledge align with Stripe’s priorities of correctness, observability, and developer velocity?",
        "Generate 3 talking points for a Stripe interviewer about Samarth’s backend engineering strengths."
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

    // --- Custom Cursor Logic ---
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

    // --- Magnetic Buttons ---
    const magneticElements = document.querySelectorAll('.magnetic-btn, .magnetic-link');
    magneticElements.forEach(el => {
        el.addEventListener('mousemove', function (e) {
            const rect = el.getBoundingClientRect();
            const x = e.clientX - rect.left - rect.width / 2;
            const y = e.clientY - rect.top - rect.height / 2;

            el.style.transform = `translate(${x * 0.3}px, ${y * 0.3}px)`;
        });

        el.addEventListener('mouseleave', function () {
            el.style.transform = 'translate(0, 0)';
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
    });

    // --- Particles.js Config ---
    if (document.getElementById('particles-js')) {
        particlesJS("particles-js", {
            "particles": {
                "number": {
                    "value": 80, /* Increased from 40 */
                    "density": {
                        "enable": true,
                        "value_area": 800
                    }
                },
                "color": {
                    "value": "#ffffff"
                },
                "shape": {
                    "type": "circle",
                    "stroke": {
                        "width": 0,
                        "color": "#000000"
                    },
                    "polygon": {
                        "nb_sides": 5
                    }
                },
                "opacity": {
                    "value": 0.5, /* Increased from 0.1 */
                    "random": true,
                    "anim": {
                        "enable": false,
                        "speed": 1,
                        "opacity_min": 0.1,
                        "sync": false
                    }
                },
                "size": {
                    "value": 5, /* Increased from 3 */
                    "random": true,
                    "anim": {
                        "enable": false,
                        "speed": 40,
                        "size_min": 0.1,
                        "sync": false
                    }
                },
                "line_linked": {
                    "enable": true,
                    "distance": 150,
                    "color": "#ffffff",
                    "opacity": 0.4, /* Increased from 0.05 */
                    "width": 1
                },
                "move": {
                    "enable": true,
                    "speed": 2, /* Increased from 1 */
                    "direction": "none",
                    "random": false,
                    "straight": false,
                    "out_mode": "out",
                    "bounce": false,
                    "attract": {
                        "enable": false,
                        "rotateX": 600,
                        "rotateY": 1200
                    }
                }
            },
            "interactivity": {
                "detect_on": "canvas",
                "events": {
                    "onhover": {
                        "enable": true,
                        "mode": "grab"
                    },
                    "onclick": {
                        "enable": true,
                        "mode": "push"
                    },
                    "resize": true
                },
                "modes": {
                    "grab": {
                        "distance": 140,
                        "line_linked": {
                            "opacity": 0.6 /* Increased from 0.15 */
                        }
                    },
                    "bubble": {
                        "distance": 400,
                        "size": 40,
                        "duration": 2,
                        "opacity": 8,
                        "speed": 3
                    },
                    "repulse": {
                        "distance": 200,
                        "duration": 0.4
                    },
                    "push": {
                        "particles_nb": 4
                    },
                    "remove": {
                        "particles_nb": 2
                    }
                }
            },
            "retina_detect": true
        });
    }

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

                // Update DOM
                document.getElementById('lc-total').textContent = totalCount;
                document.getElementById('lc-easy').textContent = easyCount;
                document.getElementById('lc-medium').textContent = mediumCount;
                document.getElementById('lc-hard').textContent = hardCount;

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
        const username = 'SamarthMahendraneu';

        try {
            // Fetch user data
            const userResponse = await fetch(`https://api.github.com/users/${username}`);
            const userData = await userResponse.json();

            // Update basic stats
            document.getElementById('gh-repos').textContent = userData.public_repos || 0;

            // Fetch contributions from last year
            const currentYear = new Date().getFullYear();
            const contributionsResponse = await fetch(`https://github-contributions-api.jogruber.de/v4/${username}?y=${currentYear}`);
            const contributionsData = await contributionsResponse.json();

            // Calculate total contributions for current year
            const totalContributions = contributionsData.total?.[currentYear] || 0;
            document.getElementById('gh-contributions').textContent = totalContributions;

            // Fetch all repos to calculate stars and languages
            const reposResponse = await fetch(`https://api.github.com/users/${username}/repos?per_page=100`);
            const repos = await reposResponse.json();

            // Calculate total stars
            const totalStars = repos.reduce((sum, repo) => sum + (repo.stargazers_count || 0), 0);
            document.getElementById('gh-stars').textContent = totalStars;

            // Calculate top languages
            const languages = {};
            repos.forEach(repo => {
                if (repo.language) {
                    languages[repo.language] = (languages[repo.language] || 0) + 1;
                }
            });

            // Sort and get top 5 languages
            const topLanguages = Object.entries(languages)
                .sort((a, b) => b[1] - a[1])
                .slice(0, 5)
                .map(([lang]) => lang);

            // Display top languages
            const langTagsContainer = document.getElementById('gh-lang-tags');
            langTagsContainer.innerHTML = topLanguages.map(lang =>
                `<span>${lang}</span>`
            ).join('');

        } catch (error) {
            console.error('Error fetching GitHub stats:', error);
            document.getElementById('gh-repos').textContent = '50+';
            document.getElementById('gh-stars').textContent = '100+';
            document.getElementById('gh-contributions').textContent = '500+';
            document.getElementById('gh-lang-tags').innerHTML = '<span>Python</span><span>JavaScript</span><span>TypeScript</span>';
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
});
