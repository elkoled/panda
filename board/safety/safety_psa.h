// Safety-relevant CAN messages for PSA vehicles.
#define PSA_DRIVER               1390 // RX from BSI, Gas pedal
#define PSA_DAT_BSI              1042 // RX from BSI, Doors
#define PSA_LANE_KEEP_ASSIST     1010 // TX from OP, LKAS EPS

// Messages on the ADAS bus.
#define PSA_HS2_DYN_ABR_38D      909  // RX from CAN1, Speed
#define PSA_HS2_DAT_MDD_CMD_452  1106 // RX from CAN1, Cruise state

// CAN bus numbers.
#define PSA_MAIN_BUS 0U
#define PSA_ADAS_BUS 1U
#define PSA_CAM_BUS  2U

const CanMsg PSA_TX_MSGS[] = {
  {PSA_LANE_KEEP_ASSIST, PSA_CAM_BUS, 8},
};

RxCheck psa_rx_checks[] = {
  // TODO: counters and checksums
  {.msg = {{PSA_DRIVER, PSA_MAIN_BUS, 6, .frequency = 10U}, { 0 }, { 0 }}}, // no counter
  {.msg = {{PSA_DAT_BSI, PSA_MAIN_BUS, 8, .frequency = 20U}, { 0 }, { 0 }}}, // no counter
  {.msg = {{PSA_HS2_DYN_ABR_38D, PSA_ADAS_BUS, 8, .frequency = 25U}, { 0 }, { 0 }}},
  {.msg = {{PSA_HS2_DAT_MDD_CMD_452, PSA_ADAS_BUS, 6, .frequency = 20U}, { 0 }, { 0 }}},
};

static bool psa_lkas_msg_check(int addr) {
  return addr == PSA_LANE_KEEP_ASSIST;
}

// TODO: update rate limits
const SteeringLimits PSA_STEERING_LIMITS = {
    .angle_deg_to_can = 10,
    .angle_rate_up_lookup = {
    {0., 5., 15.},
    {10., 1.6, .30},
  },
  .angle_rate_down_lookup = {
    {0., 5., 15.},
    {10., 7.0, .8},
  },
  };

static void psa_rx_hook(const CANPacket_t *to_push) {
  int bus = GET_BUS(to_push);
  int addr = GET_ADDR(to_push);

  if (bus == PSA_CAM_BUS) {
    // Update brake pedal
    if (addr == PSA_DAT_BSI) {
      // Signal: P013_MainBrake
      brake_pressed = GET_BIT(to_push, 5);
    }
    // Update gas pedal
    if (addr == PSA_DRIVER) {
      // Signal: GAS_PEDAL
      gas_pressed = GET_BYTE(to_push, 3) > 0U;
    }

    bool stock_ecu_detected = psa_lkas_msg_check(addr);
    generic_rx_checks(stock_ecu_detected);
  }

  if (bus == PSA_ADAS_BUS) {
    // Update vehicle speed and in motion state
    if (addr == PSA_HS2_DYN_ABR_38D) {
      // Signal: VITESSE_VEHICULE_ROUES
      int speed = (GET_BYTE(to_push, 0) << 8) | GET_BYTE(to_push, 1);
      vehicle_moving = speed > 0;
      UPDATE_VEHICLE_SPEED(speed * 0.01);
    }
    // Update cruise state
    if (addr == PSA_HS2_DAT_MDD_CMD_452) {
      // Signal: DDE_ACTIVATION_RVV_ACC
      pcm_cruise_check(GET_BIT(to_push, 23));
    }
  }
}

static bool psa_tx_hook(const CANPacket_t *to_send) {
  bool tx = true;
  int addr = GET_ADDR(to_send);

  // TODO: Safety check for cruise buttons
  // TODO: check resume is not pressed when controls not allowed
  // TODO: check cancel is not pressed when cruise isn't engaged

  // Safety check for LKA
  if (addr == PSA_LANE_KEEP_ASSIST) {
    // Signal: ANGLE
    int desired_angle = to_signed((GET_BYTE(to_send, 6) << 6) | ((GET_BYTE(to_send, 7) & 0xFCU) >> 2), 14);
    // Signal: STATUS
    bool lka_active = ((GET_BYTE(to_send, 4) & 0x18U) >> 3) == 2U;

    if (steer_angle_cmd_checks(desired_angle, lka_active, PSA_STEERING_LIMITS)) {
      // TODO: uncomment when STEERING_LIMITS are aligned
      // tx = false;
    }
  }
  return tx;
}

static int psa_fwd_hook(int bus_num, int addr) {
  int bus_fwd = -1;
  switch (bus_num) {
    case PSA_MAIN_BUS: {
      if (psa_lkas_msg_check(addr)) {
        // Block stock LKAS messages
        bus_fwd = -1;
      } else {
        // Forward all other traffic from MAIN to CAM
        bus_fwd = PSA_CAM_BUS;
      }
      break;
    }
    case PSA_CAM_BUS: {
      // Forward all traffic from CAM to MAIN
      bus_fwd = PSA_MAIN_BUS;
      break;
    }
    default: {
      // No other buses should be in use; fallback to block
      bus_fwd = -1;
      break;
    }
  }
  return bus_fwd;
}

static safety_config psa_init(uint16_t param) {
  UNUSED(param);
  print("psa_init\n");
  return BUILD_SAFETY_CFG(psa_rx_checks, PSA_TX_MSGS);
}

const safety_hooks psa_hooks = {
  .init = psa_init,
  .rx = psa_rx_hook,
  .tx = psa_tx_hook,
  .fwd = psa_fwd_hook,
};
