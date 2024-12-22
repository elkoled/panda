void can_send(CANPacket_t *to_push, uint8_t bus_number, bool skip_tx_hook);

CANPacket_t can_sync = (CANPacket_t){0};

// Safety-relevant CAN messages for PSA vehicles.
#define PSA_DRIVER               1390
#define PSA_DAT_BSI              1042
#define PSA_LANE_KEEP_ASSIST     1010
// Messages on the ADAS bus.
#define PSA_HS2_DYN_ABR_38D      909
// # TODO message not in route
//#define PSA_HS2_BGE_DYN5_CMM_228 552
#define PSA_HS2_DAT_MDD_CMD_452  1106

// CAN bus numbers.
#define PSA_MAIN_BUS 0U
#define PSA_ADAS_BUS 1U
#define PSA_CAM_BUS  2U

const CanMsg PSA_TX_MSGS[] = {
  {PSA_LANE_KEEP_ASSIST, PSA_CAM_BUS, 8},
};

RxCheck psa_rx_checks[] = {
  // TODO: counters and checksums
  {.msg = {{PSA_DRIVER, PSA_MAIN_BUS, 6, .frequency = 10U}, { 0 }, { 0 }}},
  {.msg = {{PSA_DAT_BSI, PSA_MAIN_BUS, 8, .frequency = 20U}, { 0 }, { 0 }}},
  {.msg = {{PSA_HS2_DYN_ABR_38D, PSA_ADAS_BUS, 8, .frequency = 25U}, { 0 }, { 0 }}},
  //{.msg = {{PSA_HS2_BGE_DYN5_CMM_228, PSA_ADAS_BUS, 8, .frequency = 100U}, { 0 }, { 0 }}}, //TODO: not in route
  {.msg = {{PSA_HS2_DAT_MDD_CMD_452, PSA_ADAS_BUS, 6, .frequency = 20U}, { 0 }, { 0 }}},
};

static bool psa_lkas_msg_check(int addr) {
  return addr == PSA_LANE_KEEP_ASSIST;
}

// TODO: update rate limits, copied from toyota
const SteeringLimits PSA_STEERING_LIMITS = {
    .max_steer = 100,
    .max_rate_up = 10,          // ramp up slow
    .max_rate_down = 20,        // ramp down fast
    .max_torque_error = 100,    // max torque cmd in excess of motor torque
    .max_rt_delta = 450,        // the real time limit is 1800/sec, a 20% buffer
    .max_rt_interval = 250000,
    .type = TorqueMotorLimited,

    // the EPS faults when the steering angle rate is above a certain threshold for too long. to prevent this,
    // we allow setting STEER_REQUEST bit to 0 while maintaining the requested torque value for a single frame
    // .min_valid_request_frames = 18,
    // .max_invalid_request_frames = 1,
    // .min_valid_request_rt_interval = 170000,  // 170ms; a ~10% buffer on cutting every 19 frames
    // .has_steer_req_tolerance = true,


    // LTA angle limits
    // factor for STEER_TORQUE_SENSOR->STEER_ANGLE and STEERING_LTA->STEER_ANGLE_CMD (1 / 0.0573)
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

  // if (bus == PSA_MAIN_BUS) {
  //   if (addr == PSA_LANE_KEEP_ASSIST)
  //   {
  //     can_send(&can_sync, PSA_CAM_BUS, true);
  //   }
  // }

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
  //TODO: set to true
  bool tx = true;
  UNUSED(to_send);
  // int addr = GET_ADDR(to_send);
  // if (addr == PSA_LANE_KEEP_ASSIST)
  // {
  //   can_sync = *to_send;
  // }
  // TODO: enable tx safeety checks
  // int addr = GET_ADDR(to_send);

  // TODO: Safety check for cruise buttons
  // TODO: check resume is not pressed when controls not allowed
  // TODO: check cancel is not pressed when cruise isn't engaged

  // // Safety check for LKA
  // if (addr == PSA_LANE_KEEP_ASSIST) {
  //   // Signal: TORQUE
  //   int desired_torque = to_signed((GET_BYTES(to_send, 3, 4) & 0xFFE0) >> 5, 11);
  //   // Signal: STATUS
  //   bool lka_active = ((GET_BYTE(to_send, 4) & 0x18U) >> 3) == 2U;
  //   print("desired_torque: ");
  //   puth(desired_torque);
  //   print(" lka_active: ");
  //   puth(lka_active);
  //   print("\n\n");

  //   print("Saved LKA Message:");
  //     print("\nFD: ");
  //     puth(to_send->fd);
  //     print("\nBus: ");
  //     puth(to_send->bus);
  //     print("\nData Length Code: ");
  //     puth(to_send->data_len_code);
  //     print("\nRejected: ");
  //     puth(to_send->rejected);
  //     print("\nReturned: ");
  //     puth(to_send->returned);
  //     print("\nExtended: ");
  //     puth(to_send->extended);
  //     print("\nAddress: ");
  //     puth(to_send->addr);
  //     print("\nChecksum: ");
  //     puth(to_send->checksum);
  //     for (int i = 0; i < to_send->data_len_code; i++) {
  //       print("Data[");
  //       puth(i);
  //       print("]: ");
  //       puth(to_send->data[i]);
  //     }
  //     print("\n");

  //   if (steer_torque_cmd_checks(desired_torque, lka_active, PSA_STEERING_LIMITS)) {
  //      tx = false;
  //   }
  // }

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
