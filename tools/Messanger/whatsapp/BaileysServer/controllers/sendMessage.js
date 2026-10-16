const fs = require('fs');
const path = require('path');

const MIME_MAP = {
    '.pdf': 'application/pdf',
    '.doc': 'application/msword',
    '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    '.xls': 'application/vnd.ms-excel',
    '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    '.ppt': 'application/vnd.ms-powerpoint',
    '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    '.txt': 'text/plain',
    '.csv': 'text/csv',
    '.json': 'application/json',
    '.zip': 'application/zip',
    '.rar': 'application/vnd.rar',
    '.7z': 'application/x-7z-compressed',
    '.mp3': 'audio/mpeg',
    '.wav': 'audio/wav',
    '.ogg': 'audio/ogg; codecs=opus',
    '.m4a': 'audio/mp4'
};

module.exports = async (req, res, getSock) => {
    try {
        const sock = getSock();

        if (!req.body || Object.keys(req.body).length === 0) {
            return res.status(400).json({ error: "Empty request payload" });
        }

        const { number, message, file_path } = req.body;

        if (!number) return res.status(400).json({ error: "Number is required" });

        let cleanNumber = number.toString().replace(/[^0-9]/g, '');
        if (cleanNumber.length === 11 && cleanNumber.startsWith('0')) {
            cleanNumber = cleanNumber.slice(1);
        }
        if (cleanNumber.length === 10) {
            cleanNumber = '91' + cleanNumber;
        }
        if (cleanNumber.length < 10 || cleanNumber.length > 15) {
            return res.status(400).json({ error: "Invalid phone number format" });
        }

        if (!sock || !sock.user) {
            return res.status(503).json({ error: "WhatsApp Engine is currently offline or reconnecting." });
        }

        let targetJid = `${cleanNumber}@s.whatsapp.net`;
        try {
            const [result] = await sock.onWhatsApp(targetJid);
            if (!result || !result.exists) {
                return res.status(404).json({ error: `Phone number ${cleanNumber} is not registered on WhatsApp.` });
            }
            if (result.jid) {
                targetJid = result.jid;
            }
        } catch (verifyErr) {
            console.warn(`⚠️ [WA VERIFY WARNING] Could not verify number via onWhatsApp, proceeding with direct JID: ${verifyErr.message}`);
        }

        if (file_path) {
            if (!fs.existsSync(file_path)) {
                return res.status(400).json({ error: `Attachment file not found at path: ${file_path}` });
            }

            console.log(`📤 Preparing to send File: ${file_path} to ${targetJid}`);
            const ext = path.extname(file_path).toLowerCase();
            const fileName = path.basename(file_path);
            let messageContent = {};

            if (['.jpeg', '.jpg', '.png', '.webp'].includes(ext)) {
                messageContent = { image: { url: file_path }, caption: message || "" };
            } else if (['.mp4', '.mkv', '.mov'].includes(ext)) {
                messageContent = { video: { url: file_path }, caption: message || "" };
            } else if (['.mp3', '.ogg', '.wav', '.m4a'].includes(ext)) {
                messageContent = {
                    audio: { url: file_path },
                    mimetype: MIME_MAP[ext] || 'audio/mpeg',
                    ptt: ext === '.ogg'
                };
            } else {
                messageContent = {
                    document: { url: file_path },
                    mimetype: MIME_MAP[ext] || 'application/octet-stream',
                    fileName: fileName,
                    caption: message || ""
                };
            }

            await sock.sendMessage(targetJid, messageContent);
            console.log(`✅ File Sent Successfully!`);

            if (['.mp3', '.ogg', '.wav', '.m4a'].includes(ext) && message && message.trim()) {
                await sock.sendMessage(targetJid, { text: message.trim() });
            }
        } else if (message && message.trim()) {
            console.log(`💬 Sending Text to ${targetJid}: ${message}`);
            await sock.sendMessage(targetJid, { text: message });
            console.log(`✅ Text Sent Successfully!`);
        } else {
            return res.status(400).json({ error: "Empty message and no attachment provided" });
        }

        res.json({ success: true, status: "Message sent!" });

    } catch (error) {
        console.error("❌ [API SEND ERROR]:", error.message);
        if (!res.headersSent) res.status(500).json({ error: error.message });
    }
};