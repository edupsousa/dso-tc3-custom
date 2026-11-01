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

/*
 * FNIRSI DSO-TC3 with the custom extension firmware: oscilloscope (one analog
 * channel) and signal generator over the USB CDC port.
 */

#include <config.h>
#include <math.h>
#include <string.h>
#include "protocol.h"

#define DEFAULT_SERIALCOMM	"115200/8n1"
#define POLL_MS			15

static const uint32_t scanopts[] = {
	SR_CONF_CONN,
	SR_CONF_SERIALCOMM,
};

static const uint32_t drvopts[] = {
	SR_CONF_OSCILLOSCOPE,
	SR_CONF_SIGNAL_GENERATOR,
};

static const uint32_t devopts[] = {
	SR_CONF_CONN | SR_CONF_GET,
	SR_CONF_LIMIT_FRAMES | SR_CONF_GET | SR_CONF_SET,
	SR_CONF_LIMIT_SAMPLES | SR_CONF_GET | SR_CONF_SET,
	SR_CONF_LIMIT_MSEC | SR_CONF_GET | SR_CONF_SET,
	SR_CONF_SAMPLERATE | SR_CONF_GET | SR_CONF_SET | SR_CONF_LIST,
	SR_CONF_TIMEBASE | SR_CONF_GET | SR_CONF_SET | SR_CONF_LIST,
	SR_CONF_TRIGGER_SOURCE | SR_CONF_GET | SR_CONF_SET | SR_CONF_LIST,
	SR_CONF_TRIGGER_SLOPE | SR_CONF_GET | SR_CONF_SET | SR_CONF_LIST,
	SR_CONF_TRIGGER_LEVEL | SR_CONF_GET | SR_CONF_SET,
	SR_CONF_TRIGGER_MODE | SR_CONF_GET | SR_CONF_SET | SR_CONF_LIST,
};

static const uint32_t devopts_cg_analog[] = {
	SR_CONF_VDIV | SR_CONF_GET | SR_CONF_SET | SR_CONF_LIST,
	SR_CONF_COUPLING | SR_CONF_GET | SR_CONF_SET | SR_CONF_LIST,
	SR_CONF_PROBE_FACTOR | SR_CONF_GET | SR_CONF_SET | SR_CONF_LIST,
};

static const uint32_t devopts_cg_gen[] = {
	SR_CONF_PATTERN_MODE | SR_CONF_GET | SR_CONF_SET | SR_CONF_LIST,
	SR_CONF_OUTPUT_FREQUENCY | SR_CONF_GET | SR_CONF_SET | SR_CONF_LIST,
	SR_CONF_AMPLITUDE | SR_CONF_GET | SR_CONF_SET,
	SR_CONF_DUTY_CYCLE | SR_CONF_GET | SR_CONF_SET | SR_CONF_LIST,
};

/* T/div 1 us .. 10 s, index = the device's T/div index. */
static const uint64_t timebases[TC3_NUM_TDIV][2] = {
	{ 1, 1000000 }, { 2, 1000000 }, { 5, 1000000 },
	{ 1, 100000 }, { 2, 100000 }, { 5, 100000 },
	{ 1, 10000 }, { 2, 10000 }, { 5, 10000 },
	{ 1, 1000 }, { 2, 1000 }, { 5, 1000 },
	{ 1, 100 }, { 2, 100 }, { 5, 100 },
	{ 1, 10 }, { 2, 10 }, { 5, 10 },
	{ 1, 1 }, { 2, 1 }, { 5, 1 }, { 10, 1 },
};

static const char *trigger_sources[] = { "CH1" };
static const char *trigger_slopes[] = { "r", "f" };
static const char *couplings[] = { "AC", "DC" };
static const char *trigger_modes[TC3_NUM_TRIGMODES] = { "auto", "normal", "single" };
static const uint64_t probe_factors[] = { 1, 10 };
/* Index = the stock firmware's wave number. */
static const char *waveforms[TC3_NUM_WAVES] = { "sine", "square", "pulse", "triangle", "ramp", "dc" };

static struct sr_dev_driver fnirsi_dso_tc3_driver_info;

static struct sr_dev_inst *probe_port(const char *conn, const char *serialcomm)
{
	struct sr_dev_inst *sdi;
	struct dev_context *devc;
	struct sr_serial_dev_inst *ser;
	struct sr_channel_group *cg;
	struct sr_channel *ch;
	char banner[32];

	ser = sr_serial_dev_inst_new(conn, serialcomm);
	if (!ser)
		return NULL;
	if (serial_open(ser, SERIAL_RDWR) != SR_OK) {
		sr_serial_dev_inst_free(ser);
		return NULL;
	}

	sdi = g_malloc0(sizeof(*sdi));
	sdi->status = SR_ST_INACTIVE;
	sdi->inst_type = SR_INST_SERIAL;
	sdi->conn = ser;
	sdi->connection_id = g_strdup(conn);
	sdi->priv = devc = g_malloc0(sizeof(*devc));
	sr_sw_limits_init(&devc->limits);

	if (tc3_identify(sdi, banner, sizeof(banner)) != SR_OK) {
		sr_dbg("No DSO-TC3 extension firmware on %s.", conn);
		goto fail;
	}
	sr_info("Found %s on %s.", banner, conn);

	sdi->vendor = g_strdup("FNIRSI");
	sdi->model = g_strdup("DSO-TC3");
	sdi->version = g_strdup(banner);

	ch = sr_channel_new(sdi, 0, SR_CHANNEL_ANALOG, TRUE, "CH1");
	cg = sr_channel_group_new(sdi, "CH1", NULL);
	cg->channels = g_slist_append(cg->channels, ch);
	/* The generator has no channel: it is configured through its channel group. */
	sr_channel_group_new(sdi, "GEN", NULL);

	serial_close(ser);
	return sdi;

fail:
	serial_close(ser);
	sr_serial_dev_inst_free(ser);
	g_free(sdi->priv);
	g_free(sdi->connection_id);
	g_free(sdi);
	return NULL;
}

static GSList *scan(struct sr_dev_driver *di, GSList *options)
{
	GSList *devices, *ports, *l;
	struct sr_dev_inst *sdi;
	const char *conn, *serialcomm;

	devices = NULL;
	conn = NULL;
	serialcomm = DEFAULT_SERIALCOMM;
	(void)sr_serial_extract_options(options, &conn, &serialcomm);

	if (conn) {
		if ((sdi = probe_port(conn, serialcomm)))
			devices = g_slist_append(devices, sdi);
	} else {
		/* No port given: the USB CDC ports with the device's VID and PID. */
		ports = sr_serial_find_usb(TC3_USB_VID, TC3_USB_PID);
		for (l = ports; l; l = l->next) {
			if ((sdi = probe_port(l->data, serialcomm)))
				devices = g_slist_append(devices, sdi);
		}
		g_slist_free_full(ports, g_free);
	}

	return std_scan_complete(di, devices);
}

static int dev_open(struct sr_dev_inst *sdi)
{
	struct dev_context *devc = sdi->priv;
	int ret;

	if ((ret = std_serial_dev_open(sdi)) != SR_OK)
		return ret;
	devc->link_lost = FALSE;
	devc->errors = 0;
	if ((ret = tc3_read_state(sdi)) != SR_OK) {
		sr_err("Could not read the scope state (device error %d).", devc->last_err);
		std_serial_dev_close(sdi);
		return ret;
	}
	if (tc3_gen_read(sdi) != SR_OK)
		sr_warn("Could not read the generator state.");
	return SR_OK;
}

static int dev_close(struct sr_dev_inst *sdi)
{
	struct dev_context *devc = sdi->priv;

	g_free(devc->buf);
	devc->buf = NULL;
	devc->buf_len = 0;
	return std_serial_dev_close(sdi);
}

static int check_cg(const struct sr_dev_inst *sdi, const struct sr_channel_group *cg, gboolean *gen)
{
	if (!cg) {
		*gen = FALSE;
		return SR_OK;
	}
	if (!g_slist_find(sdi->channel_groups, cg))
		return SR_ERR_ARG;
	*gen = !cg->channels;
	return SR_OK;
}

/* 25 samples per division at every T/div; the interleaved T/div 0..2 have their own rate. */
static uint64_t tdiv_rate(unsigned int tdiv)
{
	double nominal = 25.0 / tc3_tdiv_seconds(tdiv);
	uint64_t meta;
	unsigned int dup;

	if (tdiv >= TC3_ROLL_MIN_TDIV) {
		tc3_roll_rate(nominal, &meta, &dup);	/* as sent in the samplerate meta packet */
		return meta;
	}
	return (uint64_t)llround(tc3_rate_hz(tdiv, nominal / 1000.0));
}

static uint64_t current_rate(const struct dev_context *devc)
{
	return tdiv_rate(devc->st.tdiv);
}

/* The sample rate follows the timebase: the T/div whose rate is nearest to `hz`, on a log scale. */
static int rate_tdiv_index(uint64_t hz)
{
	int i, best = 0;
	double d, bestd = HUGE_VAL;

	if (!hz)
		return -1;
	for (i = 0; i < TC3_NUM_TDIV; i++) {
		d = fabs(log((double)tdiv_rate(i) / (double)hz));
		if (d < bestd) {
			bestd = d;
			best = i;
		}
	}
	return best;
}

static int config_get(uint32_t key, GVariant **data,
	const struct sr_dev_inst *sdi, const struct sr_channel_group *cg)
{
	struct dev_context *devc;
	gboolean gen;
	int ret;

	if (!sdi)
		return SR_ERR_ARG;
	devc = sdi->priv;
	if ((ret = check_cg(sdi, cg, &gen)) != SR_OK)
		return ret;

	/* The buttons on the device change settings too: read them afresh (a failure keeps the cached values). */
	if (sdi->status == SR_ST_ACTIVE) {
		if (gen)
			tc3_gen_read(sdi);
		else if (key != SR_CONF_CONN && key != SR_CONF_LIMIT_FRAMES &&
				key != SR_CONF_LIMIT_SAMPLES && key != SR_CONF_LIMIT_MSEC)
			tc3_read_state(sdi);
	}

	if (gen) {
		switch (key) {
		case SR_CONF_PATTERN_MODE:
			if (devc->gen.wave >= TC3_NUM_WAVES)
				return SR_ERR_NA;
			*data = g_variant_new_string(waveforms[devc->gen.wave]);
			return SR_OK;
		case SR_CONF_OUTPUT_FREQUENCY:
			*data = g_variant_new_double(devc->gen.freq);
			return SR_OK;
		case SR_CONF_AMPLITUDE:
			*data = g_variant_new_double(devc->gen.amp);
			return SR_OK;
		case SR_CONF_DUTY_CYCLE:
			*data = g_variant_new_double(devc->gen.duty);
			return SR_OK;
		default:
			return SR_ERR_NA;
		}
	}

	switch (key) {
	case SR_CONF_CONN:
		if (!sdi->connection_id)
			return SR_ERR_NA;
		*data = g_variant_new_string(sdi->connection_id);
		return SR_OK;
	case SR_CONF_LIMIT_FRAMES:
	case SR_CONF_LIMIT_SAMPLES:
	case SR_CONF_LIMIT_MSEC:
		return sr_sw_limits_config_get(&devc->limits, key, data);
	case SR_CONF_SAMPLERATE:
		*data = g_variant_new_uint64(current_rate(devc));
		return SR_OK;
	case SR_CONF_TIMEBASE:
		*data = g_variant_new("(tt)", timebases[devc->st.tdiv][0], timebases[devc->st.tdiv][1]);
		return SR_OK;
	case SR_CONF_TRIGGER_SOURCE:
		*data = g_variant_new_string(trigger_sources[0]);
		return SR_OK;
	case SR_CONF_TRIGGER_SLOPE:
		*data = g_variant_new_string(trigger_slopes[devc->st.edge ? 1 : 0]);
		return SR_OK;
	case SR_CONF_TRIGGER_LEVEL:
		*data = g_variant_new_double(tc3_trigger_level(&devc->st));
		return SR_OK;
	case SR_CONF_TRIGGER_MODE:
		*data = g_variant_new_string(trigger_modes[devc->st.trigmode]);
		return SR_OK;
	case SR_CONF_VDIV: {
		uint64_t mv = (uint64_t)llround(tc3_vdiv_volts(devc->st.vdiv, devc->st.probe) * 1000.0);

		*data = g_variant_new("(tt)", mv, (uint64_t)1000);
		return SR_OK;
	}
	case SR_CONF_COUPLING:
		*data = g_variant_new_string(couplings[devc->st.dc ? 1 : 0]);
		return SR_OK;
	case SR_CONF_PROBE_FACTOR:
		*data = g_variant_new_uint64(devc->st.probe ? 10 : 1);
		return SR_OK;
	default:
		return SR_ERR_NA;
	}
}

/* Index of the V/div (at the current probe setting) nearest to `volts`, on a log scale. */
static int vdiv_index(double volts, unsigned int probe)
{
	int i, best = 0;
	double d, bestd = HUGE_VAL;

	if (volts <= 0.0)
		return -1;
	for (i = 0; i < TC3_NUM_VDIV; i++) {
		d = fabs(log(tc3_vdiv_volts(i, probe) / volts));
		if (d < bestd) {
			bestd = d;
			best = i;
		}
	}
	return best;
}

/* Index of the timebase equal to p/q; 10 us arrives as 10/1000000 and is listed as 1/100000. */
static int timebase_index(GVariant *data)
{
	uint64_t p, q;
	unsigned int i;

	g_variant_get(data, "(tt)", &p, &q);
	for (i = 0; i < ARRAY_SIZE(timebases); i++) {
		if (p * timebases[i][1] == q * timebases[i][0])
			return i;
	}
	return -1;
}

static int gen_apply(const struct sr_dev_inst *sdi, const struct tc3_gen *gen)
{
	struct dev_context *devc = sdi->priv;
	int ret;

	ret = tc3_gen_write(sdi, gen);
	if (ret != SR_OK) {
		sr_err("The generator refused the setting (device error %d).", devc->last_err);
		return ret;
	}
	devc->gen = *gen;
	return SR_OK;
}

static int config_set(uint32_t key, GVariant *data,
	const struct sr_dev_inst *sdi, const struct sr_channel_group *cg)
{
	struct dev_context *devc;
	struct tc3_gen gen;
	gboolean is_gen;
	uint64_t p, q;
	double d;
	int idx, ret;

	if (!sdi)
		return SR_ERR_ARG;
	devc = sdi->priv;
	if ((ret = check_cg(sdi, cg, &is_gen)) != SR_OK)
		return ret;

	if (is_gen) {
		gen = devc->gen;
		switch (key) {
		case SR_CONF_PATTERN_MODE:
			if ((idx = std_str_idx(data, ARRAY_AND_SIZE(waveforms))) < 0)
				return SR_ERR_ARG;
			gen.wave = idx;
			break;
		case SR_CONF_OUTPUT_FREQUENCY:
			d = g_variant_get_double(data);
			if (d < TC3_GEN_FREQ_MIN || d > TC3_GEN_FREQ_MAX)
				return SR_ERR_ARG;
			gen.freq = (uint32_t)llround(d);
			break;
		case SR_CONF_AMPLITUDE:
			d = g_variant_get_double(data);
			if (d < TC3_GEN_AMP_MIN || d > TC3_GEN_AMP_MAX)
				return SR_ERR_ARG;
			gen.amp = d;
			break;
		case SR_CONF_DUTY_CYCLE:
			d = g_variant_get_double(data);
			if (d < 0.0 || d > 100.0)
				return SR_ERR_ARG;
			gen.duty = (uint8_t)llround(d);
			break;
		default:
			return SR_ERR_NA;
		}
		return gen_apply(sdi, &gen);
	}

	switch (key) {
	case SR_CONF_LIMIT_FRAMES:
	case SR_CONF_LIMIT_SAMPLES:
	case SR_CONF_LIMIT_MSEC:
		return sr_sw_limits_config_set(&devc->limits, key, data);
	case SR_CONF_TIMEBASE:
		if ((idx = timebase_index(data)) < 0)
			return SR_ERR_ARG;
		ret = tc3_ui_set(sdi, TC3_UI_TDIV, idx);
		break;
	case SR_CONF_SAMPLERATE:
		if ((idx = rate_tdiv_index(g_variant_get_uint64(data))) < 0)
			return SR_ERR_ARG;
		ret = tc3_ui_set(sdi, TC3_UI_TDIV, idx);
		break;
	case SR_CONF_TRIGGER_SOURCE:
		if (std_str_idx(data, ARRAY_AND_SIZE(trigger_sources)) < 0)
			return SR_ERR_ARG;
		return SR_OK;	/* CH1 is the only source */
	case SR_CONF_TRIGGER_SLOPE:
		if ((idx = std_str_idx(data, ARRAY_AND_SIZE(trigger_slopes))) < 0)
			return SR_ERR_ARG;
		ret = tc3_ui_set(sdi, TC3_UI_EDGE, idx);
		break;
	case SR_CONF_TRIGGER_LEVEL:
		tc3_read_state(sdi);	/* the offset depends on position, V/div and probe, which the buttons may have changed */
		ret = tc3_ui_set(sdi, TC3_UI_TRIGLEVEL, tc3_trigger_offset(&devc->st, g_variant_get_double(data)));
		break;
	case SR_CONF_TRIGGER_MODE:
		if ((idx = std_str_idx(data, ARRAY_AND_SIZE(trigger_modes))) < 0)
			return SR_ERR_ARG;
		ret = tc3_ui_set(sdi, TC3_UI_TRIGMODE, idx);
		break;
	case SR_CONF_VDIV:
		g_variant_get(data, "(tt)", &p, &q);
		tc3_read_state(sdi);	/* the list follows the probe setting, which the buttons may have changed */
		if (!q || (idx = vdiv_index((double)p / q, devc->st.probe)) < 0)
			return SR_ERR_ARG;
		ret = tc3_ui_set(sdi, TC3_UI_VDIV, idx);
		break;
	case SR_CONF_COUPLING:
		if ((idx = std_str_idx(data, ARRAY_AND_SIZE(couplings))) < 0)
			return SR_ERR_ARG;
		ret = tc3_ui_set(sdi, TC3_UI_COUPLING, idx);
		break;
	case SR_CONF_PROBE_FACTOR:
		if ((idx = std_u64_idx(data, ARRAY_AND_SIZE(probe_factors))) < 0)
			return SR_ERR_ARG;
		ret = tc3_ui_set(sdi, TC3_UI_PROBE, idx);
		break;
	default:
		return SR_ERR_NA;
	}
	if (ret != SR_OK) {
		sr_err("The device refused the setting (device error %d; settings need the scope view).", devc->last_err);
		return ret;
	}
	/* Pick up what the device actually did. */
	tc3_read_state(sdi);
	return SR_OK;
}

static int config_list(uint32_t key, GVariant **data,
	const struct sr_dev_inst *sdi, const struct sr_channel_group *cg)
{
	struct dev_context *devc = sdi ? sdi->priv : NULL;
	GVariantBuilder gvb;
	gboolean gen = FALSE;
	int i;

	if (sdi && cg && !g_slist_find(sdi->channel_groups, cg))
		return SR_ERR_ARG;
	if (cg)
		gen = !cg->channels;

	if (!cg) {
		switch (key) {
		case SR_CONF_SCAN_OPTIONS:
		case SR_CONF_DEVICE_OPTIONS:
			return STD_CONFIG_LIST(key, data, sdi, cg, scanopts, drvopts, devopts);
		case SR_CONF_SAMPLERATE: {
			/* One rate per timebase, ascending (PulseView shows its sample rate box only for a list). */
			uint64_t rates[TC3_NUM_TDIV];
			unsigned int n = 0;

			for (i = TC3_NUM_TDIV - 1; i >= 0; i--) {
				uint64_t r = tdiv_rate(i);

				if (!n || r > rates[n - 1])
					rates[n++] = r;
			}
			*data = std_gvar_samplerates(rates, n);
			return SR_OK;
		}
		case SR_CONF_TIMEBASE:
			*data = std_gvar_tuple_array(timebases, ARRAY_SIZE(timebases));
			return SR_OK;
		case SR_CONF_TRIGGER_SOURCE:
			*data = g_variant_new_strv(trigger_sources, ARRAY_SIZE(trigger_sources));
			return SR_OK;
		case SR_CONF_TRIGGER_SLOPE:
			*data = g_variant_new_strv(trigger_slopes, ARRAY_SIZE(trigger_slopes));
			return SR_OK;
		case SR_CONF_TRIGGER_MODE:
			*data = g_variant_new_strv(trigger_modes, ARRAY_SIZE(trigger_modes));
			return SR_OK;
		default:
			return SR_ERR_NA;
		}
	}

	if (gen) {
		switch (key) {
		case SR_CONF_DEVICE_OPTIONS:
			*data = std_gvar_array_u32(ARRAY_AND_SIZE(devopts_cg_gen));
			return SR_OK;
		case SR_CONF_PATTERN_MODE:
			*data = g_variant_new_strv(waveforms, ARRAY_SIZE(waveforms));
			return SR_OK;
		case SR_CONF_OUTPUT_FREQUENCY:
			*data = std_gvar_min_max_step(TC3_GEN_FREQ_MIN, TC3_GEN_FREQ_MAX, 1.0);
			return SR_OK;
		case SR_CONF_DUTY_CYCLE:
			*data = std_gvar_min_max_step(0.0, 100.0, 1.0);
			return SR_OK;
		default:
			return SR_ERR_NA;
		}
	}

	switch (key) {
	case SR_CONF_DEVICE_OPTIONS:
		*data = std_gvar_array_u32(ARRAY_AND_SIZE(devopts_cg_analog));
		return SR_OK;
	case SR_CONF_COUPLING:
		*data = g_variant_new_strv(couplings, ARRAY_SIZE(couplings));
		return SR_OK;
	case SR_CONF_PROBE_FACTOR:
		*data = std_gvar_array_u64(ARRAY_AND_SIZE(probe_factors));
		return SR_OK;
	case SR_CONF_VDIV:
		/* V/div as shown on the screen: the scope input range times the probe setting. */
		g_variant_builder_init(&gvb, G_VARIANT_TYPE_ARRAY);
		for (i = 0; i < TC3_NUM_VDIV; i++) {
			uint64_t mv = (uint64_t)llround(tc3_vdiv_volts(i, devc ? devc->st.probe : 0) * 1000.0);

			g_variant_builder_add_value(&gvb,
				g_variant_new("(tt)", mv, (uint64_t)1000));
		}
		*data = g_variant_builder_end(&gvb);
		return SR_OK;
	default:
		return SR_ERR_NA;
	}
}

static int dev_acquisition_start(const struct sr_dev_inst *sdi)
{
	struct dev_context *devc = sdi->priv;
	int ret;

	if ((ret = tc3_read_state(sdi)) != SR_OK)
		return ret;
	if (devc->st.mode != TC3_MODE_SCOPE) {
		sr_err("The device is not in the scope view (screen mode %u).", devc->st.mode);
		return SR_ERR;
	}

	sr_sw_limits_acquisition_start(&devc->limits);
	devc->last_seq = 0;
	devc->roll_active = FALSE;
	devc->sent_rate = 0;

	std_session_send_df_header(sdi);
	return sr_session_source_add(sdi->session, -1, 0, POLL_MS,
		fnirsi_dso_tc3_receive_data, (void *)sdi);
}

static int dev_acquisition_stop(struct sr_dev_inst *sdi)
{
	sr_session_source_remove(sdi->session, -1);
	std_session_send_df_end(sdi);
	return SR_OK;
}

static struct sr_dev_driver fnirsi_dso_tc3_driver_info = {
	.name = "fnirsi-dso-tc3",
	.longname = "FNIRSI DSO-TC3 (extension firmware)",
	.api_version = 1,
	.init = std_init,
	.cleanup = std_cleanup,
	.scan = scan,
	.dev_list = std_dev_list,
	.dev_clear = std_dev_clear,
	.config_get = config_get,
	.config_set = config_set,
	.config_list = config_list,
	.dev_open = dev_open,
	.dev_close = dev_close,
	.dev_acquisition_start = dev_acquisition_start,
	.dev_acquisition_stop = dev_acquisition_stop,
	.context = NULL,
};
SR_REGISTER_DEV_DRIVER(fnirsi_dso_tc3_driver_info);
