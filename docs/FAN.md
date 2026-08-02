<p align="center">
  <a href="#"><img src="assets/smartir_fan.png" width="350" alt="HubIR Media Player"></a>
</p>

For this platform to work, we need a .json file containing all the necessary IR or RF commands.
Find your device's brand code [here](FAN.md#available-codes-for-fan-devices) and add the number in the `device_code` field. The compoenent will download it to the correct folder. If your device is not working, you will need to learn your own codes and place the .json file in `hub_ir/codes/fan/` subfolders. Please note that the `device_code` field only accepts positive numbers. The .json extension is not required.

## Configuration variables

**name** (Optional): The name of the device<br />
**unique_id** (Optional): An ID that uniquely identifies this device. If two devices have the same unique ID, Home Assistant will raise an exception.<br />
**device_code** (Required): ...... (Accepts only positive numbers)<br />
**controller_data** (Required): The `entity_id` of the Broadlink remote (must be an already configured device).<br />
**delay** (Optional): Adjusts the delay in seconds between multiple commands. The default is 0.5 <br />
**power_sensor** (Optional): *entity_id* for a sensor that monitors whether your device is actually On or Off. This may be a power monitor sensor. (Accepts only on/off states)<br />

## Example (using broadlink controller)

Add a Broadlink RM device named "Bedroom" via config flow (read the [docs](https://www.home-assistant.io/integrations/broadlink/)).

```yaml
hub_ir:

fan:
  - platform: hub_ir
    name: Bedroom fan
    unique_id: bedroom_fan
    device_code: 1000
    controller_data: remote.bedroom_remote
    power_sensor: binary_sensor.fan_power
```

## Available codes for Fan devices

The following are the code files created by the amazing people in the community. Before you start creating your own code file, try if one of them works for your device. **Please open an issue if your device is working and not included in the supported models.**
Contributing to your own code files is welcome. Incomplete files are not accepted: run `python scripts/validate_codes.py` before opening a pull request.

#### Kaze

| Code | Supported Models | Controller |
| ------------- | -------------------------- | ------------- |
[1000](../codes/fan/1000.json)|Unknown|Broadlink

#### Acorn

| Code | Supported Models | Controller |
| ------------- | -------------------------- | ------------- |
[1020](../codes/fan/1020.json)|Unknown|Broadlink

#### Atomberg

| Code | Supported Models | Notes |Controller |
| ------------- | ----- | ----- | ------------- |
[1160](../codes/fan/1160.json)|Efficio||Broadlink
[1170](../codes/fan/1170.json)|Renesa|Speeds `1,2,3,4,5` is mapped to `2,3,4,5,Boost` on the remote|Broadlink

#### Lucci Air

| Code | Supported Models | Controller |
| ------------- | -------------------------- | ------------- |
[1040](../codes/fan/1040.json)|Aria|Broadlink
[1041](../codes/fan/1041.json)|Whitehaven DC|Broadlink

#### Super Fan

| Code | Supported Models | Controller |
| ------------- | -------------------------- | ------------- |
[1060](../codes/fan/1060.json)|A1|Broadlink

#### Harbor Breeze

| Code | Supported Models | Controller |
| ------------- | -------------------------- | ------------- |
[1080](../codes/fan/1080.json)|A25-TX001-R1|Broadlink
[1081](../codes/fan/1081.json)|A25-TX025|Broadlink

#### Pacific

| Code | Supported Models | Controller |
| ------------- | -------------------------- | ------------- |
[1100](../codes/fan/1100.json)|Unknown|Broadlink

#### Europace

| Code | Supported Models | Controller |
| ------------- | -------------------------- | ------------- |
[1120](../codes/fan/1120.json)|Unknown|Broadlink

#### SMC

| Code | Supported Models | Controller |
| ------------- | -------------------------- | ------------- |
[1140](../codes/fan/1140.json)|SP486, SP483|Broadlink

#### Argo

| Code | Supported Models | Controller |
| ------------- | -------------------------- | ------------- |
[1180](../codes/fan/1180.json)|Standy|Broadlink

#### DCG

| Code | Supported Models | Controller |
| ------------- | -------------------------- | ------------- |
[1200](../codes/fan/1200.json)|Unknown|Broadlink

#### Mitsubishi

| Code | Supported Models | Controller |
| ------------- | -------------------------- | ------------- |
[1220](../codes/fan/1220.json)|C56-RW5|Broadlink

#### Mallory

| Code | Supported Models | Controller |
| ------------- | -------------------------- | ------------- |
[1240](../codes/fan/1240.json)|Air Timer TS+|Broadlink
