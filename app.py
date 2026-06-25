import time
import threading
import os
from flask import Flask, render_template, request, jsonify
import oci

app = Flask(__name__)

# Persistent rolling history storage setup
global_logs = []
logs_lock = threading.Lock()

def add_log(message):
    timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
    formatted_line = f"[{timestamp}] {message}"
    print(formatted_line)
    with logs_lock:
        global_logs.append(formatted_line)
        if len(global_logs) > 200: # Retain a clean, secure historical size limit
            global_logs.pop(0)

def run_automated_creation(config, account_config, compute_client, network_client, identity_client):
    add_log(f"🕵️ Scanning structural infrastructure topology inside target region: {config['region']}...")
    try:
        ads = identity_client.list_availability_domains(config["tenancy"]).data
        ad_name = ads[0].name if ads else ""

        vcns = network_client.list_vcns(compartment_id=config["tenancy"]).data
        if not vcns:
            add_log("❌ Error: Could not locate a valid active VCN network structure topology.")
            return
        
        subnets = network_client.list_subnets(compartment_id=config["tenancy"], vcn_id=vcns[0].id).data
        if not subnets:
            add_log("❌ Error: No default network subnet spaces mapped in target environment container.")
            return
        subnet_id = subnets[0].id

        images = compute_client.list_images(compartment_id=config["tenancy"], operating_system="Ubuntu").data
        images.sort(key=lambda img: img.time_created if img.time_created else "", reverse=True)
        
        image_id = ""
        is_arm = account_config['shape'] == "VM.Standard.A1.Flex"
        
        for img in images:
            if img.lifecycle_state != "AVAILABLE":
                continue
                
            if is_arm and "aarch64" in img.display_name.lower():
                image_id = img.id
                add_log(f"Selected Latest Ubuntu ARM Image: {img.display_name}")
                break
            elif not is_arm and "amd64" in img.display_name.lower():
                image_id = img.id
                add_log(f"Selected Latest Ubuntu AMD Image: {img.display_name}")
                break
        
        if not image_id and images: 
            image_id = images[0].id
            add_log(f"Fallback to first available image: {images[0].display_name}")

        if not image_id:
            add_log("❌ Error: No valid AVAILABLE Ubuntu images found in this region catalog.")
            return

        add_log(f"✨ Target Assets Identified -> Subnet: {subnet_id[:15]}... | Image: {image_id[:15]}... | Data-Center: {ad_name}")

        shape_config = None
        if is_arm:
            shape_config = oci.core.models.LaunchInstanceShapeConfigDetails(
                ocpus=int(account_config['ocpus']), memory_in_gbs=int(account_config['memory'])
            )

        instance_details = oci.core.models.LaunchInstanceDetails(
            compartment_id=config["tenancy"],
            availability_domain=ad_name,
            shape=account_config['shape'],
            shape_config=shape_config,
            source_details=oci.core.models.InstanceSourceViaImageDetails(
                image_id=image_id, boot_volume_size_in_gbs=int(account_config['boot_volume_gb'])
            ),
            create_vnic_details=oci.core.models.CreateVnicDetails(subnet_id=subnet_id, assign_public_ip=True),
            metadata={"ssh_authorized_keys": account_config['ssh_key']},
            display_name=account_config['display_name']
        )

        add_log(f"🚀 Kicking off background infinite provisioning execution pool for '{account_config['display_name']}'...")
        while True:
            try:
                add_log(f"⏰ Sending instance generation payload context to Oracle endpoints...")
                compute_client.launch_instance(instance_details)
                add_log(f"🎉 SUCCESS! Always Free Core Instance built and running!")
                break
            except oci.exceptions.ServiceError as e:
                if "Out of capacity" in str(e) or e.status in [500, 429]:
                    add_log(f"💤 Capacity busy in region '{config['region']}'. Retrying in 60s...")
                else:
                    add_log(f"⚠️ OCI API Notice Response: {e.message}")
            time.sleep(60)

    except Exception as e:
        add_log(f"❌ Structural Loop Fault Trace context: {str(e)}")

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/api/check-storage', methods=['POST'])
def check_storage():
    data = request.json
    config = {
        "user": data.get('user'), "fingerprint": data.get('fingerprint'),
        "tenancy": data.get('tenancy'), "region": data.get('region'),
        "key_content": data.get('private_key')
    }
    try:
        oci.config.validate_config(config)
        block_client = oci.core.BlockstorageClient(config)
        
        boot_volumes = block_client.list_boot_volumes(compartment_id=config["tenancy"]).data
        total_used_storage = sum([int(vol.size_in_gbs) for vol in boot_volumes if vol.lifecycle_state != "TERMINATED"])
        remaining_storage = max(0, 200 - total_used_storage)
        
        return jsonify({
            "success": True,
            "storage_used_gb": total_used_storage,
            "storage_remaining_gb": remaining_storage
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route('/api/auto-launch-loop', methods=['POST'])
def auto_launch():
    data = request.json
    config = {
        "user": data.get('user'), "fingerprint": data.get('fingerprint'),
        "tenancy": data.get('tenancy'), "region": data.get('region'),
        "key_content": data.get('private_key')
    }
    try:
        oci.config.validate_config(config)
        compute_client = oci.core.ComputeClient(config)
        network_client = oci.core.VirtualNetworkClient(config)
        identity_client = oci.identity.IdentityClient(config)
        
        thread = threading.Thread(
            target=run_automated_creation, 
            args=(config, data, compute_client, network_client, identity_client), 
            daemon=True
        )
        thread.start()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

# 🔧 FIX: Serve logs dynamically based on the current client offset instead of clearing history completely
@app.route('/api/logs', methods=['GET'])
def fetch_live_logs():
    offset = int(request.args.get('offset', 0))
    with logs_lock:
        # Return only items that have arrived after the client's known line index count
        requested_batch = global_logs[offset:]
        total_available = len(global_logs)
    return jsonify({
        "logs": requested_batch,
        "next_offset": total_available
    })

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
