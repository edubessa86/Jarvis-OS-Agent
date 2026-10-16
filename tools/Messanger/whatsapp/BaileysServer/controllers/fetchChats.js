module.exports = async (req, res, db) => {
    try {
        const { number, start_timestamp, end_timestamp } = req.body;

        if (!number || start_timestamp === undefined || end_timestamp === undefined) {
            return res.status(400).json({ error: "Missing required parameters" });
        }

        let cleanNumber = number.toString().replace(/[^0-9]/g, '');
        if (cleanNumber.length === 11 && cleanNumber.startsWith('0')) {
            cleanNumber = cleanNumber.slice(1);
        }
        if (cleanNumber.length === 10) {
            cleanNumber = '91' + cleanNumber;
        }

        const last10Digits = cleanNumber.slice(-10);
        const targetJidExact = `${cleanNumber}@s.whatsapp.net`;
        const suffixJidPattern = `%${last10Digits}@s.whatsapp.net`;

        console.log(`🔍 Fetching chats for: ${targetJidExact}`);

        const query = `
            SELECT from_me, text, timestamp FROM Messages
            WHERE (phone_number = ? OR phone_number LIKE ?)
              AND timestamp BETWEEN ? AND ?
            ORDER BY timestamp DESC
            LIMIT 250
        `;

        db.all(query, [targetJidExact, suffixJidPattern, start_timestamp, end_timestamp], (err, rows) => {
            if (err) {
                console.error("❌ [DB FETCH ERROR]:", err.message);
                return res.status(500).json({ error: "Database error" });
            }

            if (rows && rows.length > 0) {
                console.log(`📥 Found ${rows.length} messages for ${cleanNumber}.`);
                const chronologicalRows = rows.reverse();
                const formattedChats = chronologicalRows.map(row => ({
                    fromMe: row.from_me === 1,
                    text: row.text,
                    timestamp: row.timestamp
                }));
                return res.json({ success: true, messages: formattedChats });
            }

            console.log(`ℹ️ No messages found for ${cleanNumber} in the given timeframe.`);
            return res.json({ success: true, messages: [] });
        });

    } catch (error) {
        console.error("❌ [API FETCH ERROR]:", error.message);
        if (!res.headersSent) res.status(500).json({ error: error.message });
    }
};