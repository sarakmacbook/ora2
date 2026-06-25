import time
import threading
import os
from flask import Flask, render_template, request, jsonify
import oci

app = Flask(__name__)

def run_automated_creation(config, account_config, compute_client, network_client, identity_client):
    try:
        ads = identity_client.list_availability_domains(config["tenancy"]).data
        ad_name = ads[0].name if ads else ""

        vcns = network_client.list_vcns(compartment_id=config["tenancy"]).data
        if not vcns: return
        
        subnets = network_client.list_subnets(compartment_id=config["tenancy"], vcn_id=vcns[0].id).data
        if not subnets: return
        subnet_id = subnets[0].id

        images = compute_client.list_images(compartment_id=config["tenancy"], operating_system="Ubuntu").data
        image_id = ""
        is_arm = account_config['shape'] == "VM.Standard.A1.Flex"
        
        for img in images:
            if is_arm and "aarch64" in img.display_name.lower():
                image_id = img.id
                break
            elif not is_arm and "amd64" in img.display_name.lower():
                image_id = img.id
                break
        if not image_id and images: image_id = images[0].id

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

        while True:
            try:
                print(f"⏰ [{time.strftime('%Y-%m-%d %H:%M:%S')}] Attempting build structure instance...")
                compute_client.launch_instance(instance_details)
                print("🎉 SUCCESS! Free Tier Instance Provisioned successfully.")
                break
            except oci.exceptions.ServiceError as e:
                if "Out of capacity" in str(e) or e.status in [500, 429]:
                    print(f"💤 Capacity busy for shape. Retrying in 60s...")
                else:
                    print(f"⚠️ OCI Return Status: {e.message}")
            time.sleep(60)

    except Exception as e:
        print(f"❌ Automation Engine Trace failure: {str(e)}")

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/api/auto-launch-loop', methods=['POST'])
def auto_launch():
    data = request.json
    config = {
        "user": data.get('user'),
        "fingerprint": data.get('fingerprint'),
        "tenancy": data.get('tenancy'),
        "region": data.get('region'),
        "key_content": data.get('private_key')
    }
    
    try:
        oci.config.validate_config(config)
        compute_client = oci.core.ComputeClient(config)
        network_client = oci.core.VirtualNetworkClient(config)
        identity_client = oci.identity.IdentityClient(config)
        block_client = oci.core.BlockstorageClient(config)
        
        # 📊 Core Feature: Loop existing allocations to calculate total storage size used
        boot_volumes = block_client.list_boot_volumes(compartment_id=config["tenancy"]).data
        total_used_storage = sum([int(vol.size_in_gbs) for vol in boot_volumes if vol.lifecycle_state != "TERMINATED"])
        remaining_storage = max(0, 200 - total_used_storage)

        # Trigger background loop deployment tasks
        thread = threading.Thread(
            target=run_automated_creation, 
            args=(config, data, compute_client, network_client, identity_client), 
            daemon=True
        )
        thread.start()
        
        return jsonify({
            "success": True,
            "storage_used_gb": total_used_storage,
            "storage_remaining_gb": remaining_storage
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
