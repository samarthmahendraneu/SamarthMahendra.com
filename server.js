const http = require('http');
const fs = require('fs');
const path = require('path');

const PORT = 3001;
const DB_FILE = path.join(__dirname, 'practice_db.json');

const INITIAL_DB = {
    lastSyncDate: null,
    streak: 0,
    checkIns: {},
    problems: {},
    topics: {},
    techniques: {}
};

const MIME_TYPES = {
    '.html': 'text/html',
    '.css': 'text/css',
    '.js': 'text/javascript',
    '.csv': 'text/csv',
    '.png': 'image/png',
    '.json': 'application/json'
};

const server = http.createServer((req, res) => {
    // Enable CORS just in case
    res.setHeader('Access-Control-Allow-Origin', '*');
    res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
    res.setHeader('Access-Control-Allow-Headers', 'Content-Type');

    if (req.method === 'OPTIONS') {
        res.writeHead(204);
        return res.end();
    }

    if (req.method === 'GET' && req.url === '/api/db') {
        if (!fs.existsSync(DB_FILE)) {
            fs.writeFileSync(DB_FILE, JSON.stringify(INITIAL_DB, null, 2));
        }
        res.writeHead(200, { 'Content-Type': 'application/json' });
        return res.end(fs.readFileSync(DB_FILE, 'utf8'));
    }

    if (req.method === 'POST' && req.url === '/api/db') {
        let body = '';
        req.on('data', chunk => body += chunk.toString());
        req.on('end', () => {
            try {
                // Formatting JSON prettily
                const data = JSON.parse(body);
                fs.writeFileSync(DB_FILE, JSON.stringify(data, null, 2));
                res.writeHead(200, { 'Content-Type': 'application/json' });
                res.end(JSON.stringify({ success: true }));
            } catch (err) {
                console.error('Error saving DB:', err);
                res.writeHead(500, { 'Content-Type': 'application/json' });
                res.end(JSON.stringify({ error: err.message }));
            }
        });
        return;
    }

    // Serve Static Files
    let reqPath = req.url === '/' ? '/practice.html' : req.url;
    // Strip query strings
    reqPath = reqPath.split('?')[0];

    const filePath = path.join(__dirname, reqPath);
    const ext = path.extname(filePath);

    fs.access(filePath, fs.constants.R_OK, (err) => {
        if (err) {
            res.writeHead(404, { 'Content-Type': 'text/plain' });
            return res.end('File not found: ' + reqPath);
        }

        const contentType = MIME_TYPES[ext] || 'application/octet-stream';
        res.writeHead(200, { 'Content-Type': contentType });
        const readStream = fs.createReadStream(filePath);
        readStream.pipe(res);
    });
});

server.listen(PORT, () => {
    console.log(`Server running at http://localhost:${PORT}/practice.html`);
});
