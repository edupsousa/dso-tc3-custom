/*
 * This file is part of the libsigrok project.
 *
 * This program is free software: you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation, either version 3 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program.  If not, see <http://www.gnu.org/licenses/>.
 */

#include <config.h>
#include <math.h>
#include <string.h>
#include "protocol.h"
#include "cal.h"

#define XFER_TIMEOUT_MS		500
#define BANNER_TAIL_MS		20
#define UI_DONE_TIMEOUT_US	(2 * G_USEC_PER_SEC)
#define GEN_DONE_TIMEOUT_US	(3 * G_USEC_PER_SEC)
#define ACTION_POLL_US		5000
#define FRAME_CHUNK_SAMPLES	30
#define FRAME_MAX_CHUNKS	20
#define ROLL_CHUNK_SAMPLES	29
#define ROLL_CHUNKS_PER_CALL	40
#define MAX_ERRORS		10

/* Gain of the input stage per V/div index (the stock firmware's table). */
static const int gain_by_vdiv[TC3_NUM_VDIV] = { 1, 1, 2, 4, 5, 48, 48, 96, 240, 480 };
static const int div_mv[TC3_NUM_VDIV] = { 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000 };

/*
 * One lock for the port: a device is opened by one session, and its settings calls come from another thread than the
 * acquisition callback, so a request and its reply must not interleave.
 */
static GMutex xfer_lock;

static uint32_t rd_u32(const uint8_t *p)
{
	return p[0] | (p[1] << 8) | (p[2] << 16) | ((uint32_t)p[3] << 24);
}

static uint16_t rd_u16(const uint8_t *p)
{
	return p[0] | (p[1] << 8);
}

static float rd_f32(const uint8_t *p)
{
	uint32_t u = rd_u32(p);
	float f;

	memcpy(&f, &u, sizeof(f));
	return f;
}

static void wr_u32(uint8_t *p, uint32_t v)
{
	p[0] = v; p[1] = v >> 8; p[2] = v >> 16; p[3] = v >> 24;
}

/* ---- Port access. A negative return from the port is a lost connection; a short one is a timeout. */

static gboolean port_flush(const struct sr_dev_inst *sdi)
{
	struct dev_context *devc = sdi->priv;

	if (serial_flush(sdi->conn) == SR_OK)
		return TRUE;
	devc->link_lost = TRUE;
	return FALSE;
}

static gboolean port_write(const struct sr_dev_inst *sdi, const uint8_t *buf, size_t n)
{
	struct dev_context *devc = sdi->priv;
	int rc = serial_write_blocking(sdi->conn, buf, n, XFER_TIMEOUT_MS);

	if (rc < 0)
		devc->link_lost = TRUE;
	return rc == (int)n;
}

static gboolean port_read(const struct sr_dev_inst *sdi, uint8_t *buf, size_t n, int timeout_ms)
{
	struct dev_context *devc = sdi->priv;
	int rc = serial_read_blocking(sdi->conn, buf, n, timeout_ms);

	if (rc < 0)
		devc->link_lost = TRUE;
	return rc == (int)n;
}

/*
 * Bytes that follow the fixed part of a reply, for replies whose length is in the reply.
 * Returns -1 when the fixed part is not a valid head.
 */
typedef int (*extra_len_fn)(const uint8_t *head);

static int xfer_locked(const struct sr_dev_inst *sdi, const uint8_t *tx, size_t txlen,
		uint8_t *rx, size_t rxlen, uint8_t reply_id, extra_len_fn extra)
{
	struct dev_context *devc = sdi->priv;
	int more;

	devc->last_err = 0;
	if (!port_flush(sdi) || !port_write(sdi, tx, txlen) || !port_read(sdi, rx, 1, XFER_TIMEOUT_MS))
		return SR_ERR_IO;
	if (rx[0] == TC3_RESP_ERROR) {
		if (!port_read(sdi, rx + 1, 2, XFER_TIMEOUT_MS))
			return SR_ERR_IO;
		devc->last_err = rx[2];
		return SR_ERR_DATA;
	}
	if (rx[0] != reply_id) {
		sr_dbg("Unexpected reply 0x%02x to command 0x%02x.", rx[0], tx[0]);
		devc->last_err = TC3_ERR_BAD_REPLY;
		return SR_ERR_DATA;
	}
	if (rxlen > 1 && !port_read(sdi, rx + 1, rxlen - 1, XFER_TIMEOUT_MS))
		return SR_ERR_IO;
	if (extra) {
		if ((more = extra(rx)) < 0) {
			devc->last_err = TC3_ERR_BAD_REPLY;
			return SR_ERR_DATA;
		}
		if (more && !port_read(sdi, rx + rxlen, more, XFER_TIMEOUT_MS))
			return SR_ERR_IO;
	}
	return SR_OK;
}

/*
 * One request and its reply: `rxlen` bytes including the tag, plus what `extra` asks for. An error reply is three
 * bytes (0x7e, command, code) and gives SR_ERR_DATA with the code in devc->last_err. SR_ERR_IO is a timeout or a
 * lost connection (devc->link_lost).
 */
static int xfer_var(const struct sr_dev_inst *sdi, const uint8_t *tx, size_t txlen,
		uint8_t *rx, size_t rxlen, uint8_t reply_id, extra_len_fn extra)
{
	int ret;

	g_mutex_lock(&xfer_lock);
	ret = xfer_locked(sdi, tx, txlen, rx, rxlen, reply_id, extra);
	g_mutex_unlock(&xfer_lock);
	return ret;
}

static int xfer(const struct sr_dev_inst *sdi, const uint8_t *tx, size_t txlen,
		uint8_t *rx, size_t rxlen, uint8_t reply_id)
{
	return xfer_var(sdi, tx, txlen, rx, rxlen, reply_id, NULL);
}

SR_PRIV int tc3_identify(const struct sr_dev_inst *sdi, char *banner, size_t len)
{
	uint8_t cmd = TC3_CMD_INFO, rx[32];
	gboolean ok;
	int n = 0;

	g_mutex_lock(&xfer_lock);
	ok = port_flush(sdi) && port_write(sdi, &cmd, 1) && port_read(sdi, rx, 4, XFER_TIMEOUT_MS) &&
		rx[0] == TC3_CMD_INFO;
	if (ok) {
		/* Tag, version, then the banner, which has no length: it comes in the same USB packet, so a short wait gets it. */
		n = serial_read_blocking(sdi->conn, rx + 4, sizeof(rx) - 4, BANNER_TAIL_MS);
	}
	g_mutex_unlock(&xfer_lock);
	if (!ok || n < 1)
		return SR_ERR_DATA;
	g_snprintf(banner, len, "%.*s", n, (const char *)&rx[4]);
	return SR_OK;
}

static int read_mem(const struct sr_dev_inst *sdi, uint32_t addr, uint8_t *out, unsigned int n)
{
	uint8_t tx[7] = { TC3_CMD_READ_MEM }, rx[1 + TC3_READ_MEM_MAX];
	int ret;

	if (n > TC3_READ_MEM_MAX)
		return SR_ERR_ARG;
	wr_u32(&tx[1], addr);
	tx[5] = n;
	tx[6] = n >> 8;
	ret = xfer(sdi, tx, sizeof(tx), rx, 1 + n, TC3_RESP_READ_MEM);
	if (ret == SR_OK)
		memcpy(out, rx + 1, n);
	return ret;
}

/* The scope state in one transfer, so the fields are consistent and a refresh costs one round trip. */
SR_PRIV int tc3_read_state(const struct sr_dev_inst *sdi)
{
	struct dev_context *devc = sdi->priv;
	struct tc3_state st;
	uint8_t m[TC3_STATE_LEN];
	int ret;

#define FIELD(off) rd_u32(&m[(off) - TC3_STATE_FROM])
	ret = read_mem(sdi, TC3_STATE_BASE + TC3_STATE_FROM, m, sizeof(m));
	if (ret != SR_OK)
		return ret;
	st.mode = FIELD(TC3_OFF_MODE);
	st.pos = (int32_t)FIELD(TC3_OFF_POS);
	st.vdiv = FIELD(TC3_OFF_VDIV);
	st.probe = FIELD(TC3_OFF_PROBE);
	st.tdiv = FIELD(TC3_OFF_TDIV);
	st.dc = FIELD(TC3_OFF_COUPLING);
	st.trigoff = (int32_t)FIELD(TC3_OFF_TRIGOFF);
	st.trigmode = FIELD(TC3_OFF_TRIGMODE);
	st.edge = FIELD(TC3_OFF_EDGE);
#undef FIELD
	if (st.trigmode >= TC3_NUM_TRIGMODES || st.vdiv >= TC3_NUM_VDIV || st.tdiv >= TC3_NUM_TDIV || st.probe > 1) {
		devc->last_err = TC3_ERR_BAD_REPLY;
		return SR_ERR_DATA;
	}
	devc->st = st;
	return SR_OK;
}

/*
 * An action that runs in the device's main loop (UI request 0x55, generator 0x58). The device acknowledges the
 * request at once (byte 2 = 0, or an error code), then the poll request (tag, `poll_arg`) answers with the state in
 * byte 2 and the result in byte 3 until the action is done.
 */
static int request_action(const struct sr_dev_inst *sdi, const uint8_t *tx, size_t txlen, uint8_t poll_arg,
		size_t poll_reply_len, int64_t timeout_us)
{
	struct dev_context *devc = sdi->priv;
	uint8_t ack[3], poll[2] = { tx[0], poll_arg }, rx[16];
	int64_t deadline;
	int ret;

	ret = xfer(sdi, tx, txlen, ack, sizeof(ack), tx[0]);
	if (ret != SR_OK)
		return ret;
	if (ack[2] != 0) {
		devc->last_err = ack[2];
		return SR_ERR_DATA;
	}
	deadline = g_get_monotonic_time() + timeout_us;
	do {
		ret = xfer(sdi, poll, sizeof(poll), rx, poll_reply_len, tx[0]);
		if (ret != SR_OK)
			return ret;
		if (rx[2] == TC3_ACTION_DONE) {
			if (rx[3] != 0) {
				devc->last_err = rx[3];
				return SR_ERR_DATA;
			}
			return SR_OK;
		}
		g_usleep(ACTION_POLL_US);
	} while (g_get_monotonic_time() < deadline);
	return SR_ERR_IO;
}

SR_PRIV int tc3_ui_set(const struct sr_dev_inst *sdi, int field, int value)
{
	uint8_t tx[6] = { TC3_CMD_UI, field };

	wr_u32(&tx[2], (uint32_t)value);
	return request_action(sdi, tx, sizeof(tx), TC3_HEADER, 9, UI_DONE_TIMEOUT_US);
}

SR_PRIV int tc3_gen_read(const struct sr_dev_inst *sdi)
{
	struct dev_context *devc = sdi->priv;
	uint8_t tx[2] = { TC3_CMD_GEN, TC3_GEN_GET }, rx[14];
	int ret;

	ret = xfer(sdi, tx, sizeof(tx), rx, sizeof(rx), TC3_CMD_GEN);
	if (ret != SR_OK)
		return ret;
	devc->gen.wave = rx[4];
	devc->gen.freq = rd_u32(&rx[5]);
	devc->gen.duty = rx[9];
	devc->gen.amp = rd_f32(&rx[10]);
	return SR_OK;
}

SR_PRIV int tc3_gen_write(const struct sr_dev_inst *sdi, const struct tc3_gen *gen)
{
	uint8_t tx[12] = { TC3_CMD_GEN, TC3_GEN_SET, gen->wave };
	uint32_t amp_bits;

	wr_u32(&tx[3], gen->freq);
	tx[7] = gen->duty;
	memcpy(&amp_bits, &gen->amp, sizeof(amp_bits));
	wr_u32(&tx[8], amp_bits);
	return request_action(sdi, tx, sizeof(tx), TC3_GEN_GET, 14, GEN_DONE_TIMEOUT_US);
}

/* ---- Time and voltage scales. */

SR_PRIV double tc3_tdiv_seconds(unsigned int idx)
{
	static const int mant[3] = { 1, 2, 5 };

	return mant[idx % 3] * pow(10.0, -6 + (int)(idx / 3));
}

SR_PRIV double tc3_vdiv_volts(unsigned int idx, unsigned int probe)
{
	return div_mv[idx] / 1000.0 * (probe ? 10 : 1);
}

/*
 * Sample rate in Hz. T/div 0..2 run both ADCs interleaved from the ADC clock
 * (144 MHz / 14, half that at index 2); the firmware reports 10280 / 5140 kS/s,
 * which are 0.06 % low.
 */
SR_PRIV double tc3_rate_hz(unsigned int tdiv, float header_rate_ksps)
{
	if (tdiv <= 1)
		return 144e6 / 14;
	if (tdiv == 2)
		return 144e6 / 28;
	return header_rate_ksps * 1000.0;
}

/* The measured correction of the stock conversion for this V/div index and probe: true = (scope - b) / k (k == 0: none). */
static void cal_for(unsigned int vdiv, unsigned int probe, double *k, double *b)
{
	*k = probe ? tc3_cal_x10[vdiv].k : tc3_cal_x1[vdiv].k;
	*b = probe ? tc3_cal_x10[vdiv].b : tc3_cal_x1[vdiv].b;
}

/* volts = raw * a + b, for the 12-bit sample `raw`. */
struct tc3_scale {
	double a, b;
};

static struct tc3_scale scale_for(int gain, unsigned int probe, unsigned int vdiv)
{
	/* The stock conversion: 12 bits over 2.5 V, centred, divided by the stage gain (7.843137 = the front end's attenuation). */
	double pf = probe ? 10 : 1, k, b0;
	struct tc3_scale s = { 2.5 / 4095.0 * gain / 7.843137 * pf, -1.25 * gain / 7.843137 * pf };

	cal_for(vdiv, probe, &k, &b0);
	if (k != 0.0) {
		s.a /= k;
		s.b = (s.b - b0) / k;
	}
	return s;
}

/* Trigger level in volts, as the corrected samples read it. */
SR_PRIV double tc3_trigger_level(const struct tc3_state *st)
{
	double v, k, b;

	v = (st->pos - st->trigoff) * tc3_vdiv_volts(st->vdiv, st->probe) / TC3_TRACE_UNITS_PER_DIV;
	cal_for(st->vdiv, st->probe, &k, &b);
	return k != 0.0 ? (v - b) / k : v;
}

/* The offset that puts the trigger at `volts` (corrected), clamped to the device's range. */
SR_PRIV int tc3_trigger_offset(const struct tc3_state *st, double volts)
{
	double v, k, b;
	int off;

	cal_for(st->vdiv, st->probe, &k, &b);
	v = k != 0.0 ? k * volts + b : volts;
	off = st->pos - (int)lround(v / tc3_vdiv_volts(st->vdiv, st->probe) * TC3_TRACE_UNITS_PER_DIV);
	if (off > TC3_TRIG_OFF_MAX)
		off = TC3_TRIG_OFF_MAX;
	if (off < -TC3_TRIG_OFF_MAX)
		off = -TC3_TRIG_OFF_MAX;
	return off;
}

/* ---- Data packets. */

static int send_samplerate(const struct sr_dev_inst *sdi, uint64_t rate)
{
	struct dev_context *devc = sdi->priv;

	if (rate == devc->sent_rate)
		return SR_OK;
	devc->sent_rate = rate;
	return sr_session_send_meta(sdi, SR_CONF_SAMPLERATE, g_variant_new_uint64(rate));
}

static int send_analog(const struct sr_dev_inst *sdi, const float *data, size_t n)
{
	struct sr_datafeed_packet packet;
	struct sr_datafeed_analog analog;
	struct sr_analog_encoding encoding;
	struct sr_analog_meaning meaning;
	struct sr_analog_spec spec;
	int ret;

	ret = sr_analog_init(&analog, &encoding, &meaning, &spec, 3);
	if (ret != SR_OK)
		return ret;
	analog.meaning->channels = g_slist_append(NULL, sdi->channels->data);
	analog.meaning->mq = SR_MQ_VOLTAGE;
	analog.meaning->unit = SR_UNIT_VOLT;
	analog.meaning->mqflags = 0;
	analog.num_samples = n;
	analog.data = (void *)data;
	packet.type = SR_DF_ANALOG;
	packet.payload = &analog;
	ret = sr_session_send(sdi, &packet);
	g_slist_free(analog.meaning->channels);
	return ret;
}

static int ensure_buf(struct dev_context *devc, size_t n)
{
	if (devc->buf_len >= n)
		return SR_OK;
	g_free(devc->buf);
	devc->buf = g_try_malloc(n * sizeof(float));
	if (!devc->buf) {
		devc->buf_len = 0;
		return SR_ERR_MALLOC;
	}
	devc->buf_len = n;
	return SR_OK;
}

/* ---- Burst frames (T/div 0..14). */

static int frame_header(const struct sr_dev_inst *sdi, struct tc3_frame_hdr *h)
{
	uint8_t tx[6] = { TC3_CMD_FRAME, TC3_HEADER }, rx[24];	/* seq 0 = the latest frame */
	int ret;

	ret = xfer(sdi, tx, sizeof(tx), rx, sizeof(rx), TC3_CMD_FRAME);
	if (ret != SR_OK)
		return ret;
	if (rx[1] != TC3_HEADER)
		return SR_ERR_DATA;
	h->seq = rd_u32(&rx[3]);
	h->latest = rd_u32(&rx[7]);
	h->vdiv = rx[11];
	h->tdiv = rx[12];
	h->probe = rx[13];
	h->mode = rx[14];
	h->gain = (int32_t)rd_u32(&rx[15]);
	h->rate_ksps = rd_f32(&rx[19]);
	h->chunks = rx[23];
	return SR_OK;
}

static int frame_chunk(const struct sr_dev_inst *sdi, uint8_t chunk, uint32_t seq, uint16_t *out)
{
	uint8_t tx[6] = { TC3_CMD_FRAME, chunk }, rx[2 + 2 * FRAME_CHUNK_SAMPLES];
	int ret, i;

	wr_u32(&tx[2], seq);
	ret = xfer(sdi, tx, sizeof(tx), rx, sizeof(rx), TC3_CMD_FRAME);
	if (ret != SR_OK)
		return ret;
	if (rx[1] != chunk)
		return SR_ERR_DATA;
	for (i = 0; i < FRAME_CHUNK_SAMPLES; i++)
		out[i] = rd_u16(&rx[2 + 2 * i]);
	return SR_OK;
}

/*
 * Send the newest frame, if it is new. The device checks every chunk against the frame's slot after copying it,
 * so a frame that was overwritten while we read it shows up as TC3_ERR_NO_FRAME and is dropped.
 */
static int do_frame(const struct sr_dev_inst *sdi, unsigned int tdiv)
{
	struct dev_context *devc = sdi->priv;
	struct tc3_frame_hdr h;
	struct tc3_scale scale;
	uint16_t raw[FRAME_CHUNK_SAMPLES];
	size_t n, i;
	unsigned int c;
	int ret;

	devc->roll_active = FALSE;
	ret = frame_header(sdi, &h);
	if (ret == SR_ERR_DATA && devc->last_err == TC3_ERR_EMPTY)
		return SR_OK;			/* no burst since boot */
	if (ret != SR_OK)
		return ret;
	if (h.latest == devc->last_seq || h.tdiv != tdiv)
		return SR_OK;			/* nothing new, or a frame of an earlier T/div */
	if (h.chunks == 0 || h.chunks > FRAME_MAX_CHUNKS || h.vdiv >= TC3_NUM_VDIV || h.probe > 1)
		return SR_ERR_DATA;
	if (ensure_buf(devc, h.chunks * FRAME_CHUNK_SAMPLES) != SR_OK)
		return SR_ERR_MALLOC;

	scale = scale_for(h.gain, h.probe, h.vdiv);
	n = 0;
	for (c = 0; c < h.chunks; c++) {
		ret = frame_chunk(sdi, c, h.seq, raw);
		if (ret == SR_ERR_DATA && devc->last_err == TC3_ERR_NO_FRAME)
			return SR_OK;		/* the slot was overwritten: wait for the next */
		if (ret != SR_OK)
			return ret;
		for (i = 0; i < FRAME_CHUNK_SAMPLES; i++)
			devc->buf[n++] = raw[i] * scale.a + scale.b;
	}

	devc->last_seq = h.seq;
	send_samplerate(sdi, (uint64_t)llround(tc3_rate_hz(h.tdiv, h.rate_ksps)));
	std_session_send_df_frame_begin(sdi);
	send_analog(sdi, devc->buf, n);
	std_session_send_df_frame_end(sdi);
	sr_sw_limits_update_frames_read(&devc->limits, 1);
	sr_sw_limits_update_samples_read(&devc->limits, n);
	return SR_OK;
}

/* ---- Roll stream (T/div 15..21): a gap-free sample stream. */

static int roll_header(const struct sr_dev_inst *sdi, struct tc3_roll_hdr *h)
{
	uint8_t tx[6] = { TC3_CMD_ROLL, TC3_HEADER }, rx[26];
	int ret;

	ret = xfer(sdi, tx, sizeof(tx), rx, sizeof(rx), TC3_CMD_ROLL);
	if (ret != SR_OK)
		return ret;
	if (rx[1] != TC3_HEADER)
		return SR_ERR_DATA;
	h->count = rd_u32(&rx[3]);
	h->base = rd_u32(&rx[7]);
	h->epoch = rx[11];
	h->tdiv = rx[12];
	h->vdiv = rx[13];
	h->probe = rx[14];
	h->mode = rx[15];
	h->gain = (int32_t)rd_u32(&rx[16]);
	h->rate_ksps = rd_f32(&rx[20]);
	h->ring = rd_u16(&rx[24]);
	return SR_OK;
}

/* A samples reply is 0x57 1 count, then count 16-bit samples. */
static int roll_extra_len(const uint8_t *head)
{
	if (head[1] != TC3_ROLL_SAMPLES || head[2] > ROLL_CHUNK_SAMPLES)
		return -1;
	return 2 * head[2];
}

/* Up to 29 samples from absolute index `from`; each is a 12-bit value plus the V/div index in the top nibble. */
static int roll_read(const struct sr_dev_inst *sdi, uint32_t from, uint16_t *out, unsigned int *n)
{
	uint8_t tx[6] = { TC3_CMD_ROLL, TC3_ROLL_SAMPLES }, rx[3 + 2 * ROLL_CHUNK_SAMPLES];
	unsigned int i;
	int ret;

	wr_u32(&tx[2], from);
	ret = xfer_var(sdi, tx, sizeof(tx), rx, 3, TC3_CMD_ROLL, roll_extra_len);
	if (ret != SR_OK)
		return ret;
	*n = rx[2];
	for (i = 0; i < *n; i++)
		out[i] = rd_u16(&rx[3 + 2 * i]);
	return SR_OK;
}

/*
 * Samples per second at the roll T/div and how many times each sample is sent.
 * The samplerate meta is a whole number of Hz; 12.5 and 2.5 S/s are sent with
 * every sample doubled (25 and 5 Hz) so that the time axis stays exact. 125.217
 * S/s (T/div 16) stays 125 Hz, 0.17 % off.
 */
SR_PRIV void tc3_roll_rate(double hz, uint64_t *meta, unsigned int *dup)
{
	double e1 = fabs(hz - round(hz)) / hz;
	double e2 = fabs(2 * hz - round(2 * hz)) / hz;

	*dup = (e1 > 0.005 && e2 < e1 / 2) ? 2 : 1;
	*meta = (uint64_t)llround(hz * *dup);
}

/* Start the stream (or restart it after a T/div change) with the history already on the device's screen. */
static void roll_start(const struct sr_dev_inst *sdi, const struct tc3_roll_hdr *h, unsigned int tdiv, uint32_t window)
{
	struct dev_context *devc = sdi->priv;
	uint64_t meta;

	devc->roll_next = (h->count - h->base > window) ? h->count - window : h->base;
	devc->roll_epoch = h->epoch;
	devc->acq_tdiv = tdiv;
	devc->roll_active = TRUE;
	tc3_roll_rate(h->rate_ksps * 1000.0, &meta, &devc->roll_dup);
	send_samplerate(sdi, meta);
	sr_dbg("Roll stream at T/div %u: %.4f S/s, %u copies per sample, %u samples of history.",
		tdiv, h->rate_ksps * 1000.0, devc->roll_dup, h->count - devc->roll_next);
}

static int do_roll(const struct sr_dev_inst *sdi, unsigned int tdiv)
{
	struct dev_context *devc = sdi->priv;
	struct tc3_roll_hdr h;
	struct tc3_scale scale = { 0.0, 0.0 };
	uint16_t raw[ROLL_CHUNK_SAMPLES];
	unsigned int n, i, d, chunks, scale_vdiv = TC3_NUM_VDIV;
	size_t out;
	uint32_t window, nsamples;
	uint64_t remain;
	gboolean exceeded;
	int ret;

	ret = roll_header(sdi, &h);
	if (ret == SR_ERR_DATA && devc->last_err == TC3_ERR_EMPTY)
		return SR_OK;			/* no sample recorded since boot */
	if (ret != SR_OK)
		return ret;
	if (h.tdiv != tdiv || h.probe > 1 || h.ring < 8)
		return SR_OK;			/* the T/div just changed */

	window = h.ring - 4;
	if (!devc->roll_active || h.epoch != devc->roll_epoch || devc->acq_tdiv != tdiv)
		roll_start(sdi, &h, tdiv, window);
	if (h.count - devc->roll_next > window) {
		sr_warn("Roll samples lost: the host fell %u samples behind.", h.count - devc->roll_next - window);
		devc->roll_next = h.count - window;
	}

	if (ensure_buf(devc, (size_t)ROLL_CHUNKS_PER_CALL * ROLL_CHUNK_SAMPLES * 2) != SR_OK)
		return SR_ERR_MALLOC;
	out = 0;
	nsamples = 0;
	/* 0 means no sample limit, unless the limit is already exceeded. */
	sr_sw_limits_get_remain(&devc->limits, &remain, NULL, NULL, &exceeded);
	if (exceeded)
		return SR_OK;
	if (!remain)
		remain = UINT64_MAX;
	for (chunks = 0; chunks < ROLL_CHUNKS_PER_CALL && (int32_t)(h.count - devc->roll_next) > 0 && nsamples < remain; chunks++) {
		ret = roll_read(sdi, devc->roll_next, raw, &n);
		if (ret == SR_ERR_DATA && devc->last_err == TC3_ERR_NO_FRAME) {
			sr_warn("Roll samples expired before they were read.");
			devc->roll_active = FALSE;	/* start again from the current history */
			break;
		}
		if (ret != SR_OK)
			return ret;
		if (n == 0)
			break;
		for (i = 0; i < n; i++) {
			/* Each sample carries the V/div it was taken at. */
			unsigned int vdiv = raw[i] >> 12;

			if (vdiv >= TC3_NUM_VDIV)
				vdiv = h.vdiv;
			if (vdiv != scale_vdiv) {
				scale = scale_for(gain_by_vdiv[vdiv], h.probe, vdiv);
				scale_vdiv = vdiv;
			}
			for (d = 0; d < devc->roll_dup; d++)
				devc->buf[out++] = (raw[i] & 0xfff) * scale.a + scale.b;
		}
		devc->roll_next += n;
		nsamples += n;
	}
	if (nsamples > remain) {		/* do not overshoot a sample limit */
		out -= (size_t)(nsamples - remain) * devc->roll_dup;
		nsamples = remain;
	}
	if (out) {
		send_analog(sdi, devc->buf, out);
		sr_sw_limits_update_samples_read(&devc->limits, nsamples);
	}
	return SR_OK;
}

/* ---- Acquisition poll. */

SR_PRIV int fnirsi_dso_tc3_receive_data(int fd, int revents, void *cb_data)
{
	const struct sr_dev_inst *sdi = cb_data;
	struct dev_context *devc;
	int ret;

	(void)fd;
	(void)revents;
	if (!sdi || !(devc = sdi->priv))
		return TRUE;

	/* The state tells the T/div (burst or roll) and keeps the cached settings current. */
	ret = tc3_read_state(sdi);
	if (ret == SR_OK) {
		ret = (devc->st.tdiv >= TC3_ROLL_MIN_TDIV) ? do_roll(sdi, devc->st.tdiv) : do_frame(sdi, devc->st.tdiv);
	}
	if (devc->link_lost) {
		sr_err("Lost the connection to the device.");
		sr_dev_acquisition_stop((struct sr_dev_inst *)sdi);
		return TRUE;
	}
	if (ret == SR_OK) {
		devc->errors = 0;
	} else if (++devc->errors >= MAX_ERRORS) {
		sr_err("Giving up after %u failed transfers in a row (device error %d).", devc->errors, devc->last_err);
		devc->errors = 0;
		sr_dev_acquisition_stop((struct sr_dev_inst *)sdi);
		return TRUE;
	}
	if (sr_sw_limits_check(&devc->limits))
		sr_dev_acquisition_stop((struct sr_dev_inst *)sdi);
	return TRUE;
}
