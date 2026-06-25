import time
import threading
import os
from flask import Flask, render_template, request, jsonify
import oci

app = Flask(__name__)

def run_automated_creation(config, account_config, compute_client, network_client, identity_client):
    print(f"🕵️ Scanning assets inside region: {config['region']}...")
    
    try:
        # 1. Discover Availability Domain Name dynamically
        ads = identity_client.list_availability_domains(config["tenancy"]).data
        ad_name = ads[0].name if ads else ""

        # 2. Automatically locate the Default Subnet ID inside your VCN
        vcns = network_client.list_vcns(compartment_id=config["tenancy"]).data
        if not vcns:
            print("❌ Failure: Could not locate a Virtual Cloud Network (VCN). Create one first.")
            return
        
        subnets = network_client.list_subnets(compartment_id=config["tenancy"], vcn_id=vcns[0].id).data
        if not subnets:
            print("❌ Failure: No active subnets found in the target VCN.")
            return
        subnet_id = subnets[0].id

        # 3. Find latest Ubuntu image matched with the right system architecture type
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
        
        if not image_id and images:
            image_id = images[0].id

        print(f"✨ Auto-discovered Network Core -> Subnet: {subnet_id} | Image: {image_id} | Domain: {ad_name}")

        # Assemble deployment object schema parameters
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

        # Run provisioning retry execution structure
        while True:
            try:
                print(f"⏰ [{time.strftime('%Y-%m-%d %H:%M:%S')}] Launching attempt for '{account_config['display_name']}'...")
                response = compute_client.launch_instance(instance_details)
                print(f"🎉 SUCCESS! Target instance created: {response.data.id}")
                break
            except oci.exceptions.ServiceError as e:
                if "Out of capacity" in str(e) or e.status in [500, 429]:
                    print(f"💤 Capacity busy in region '{config['region']}'. Retrying in 60s...")
                else:
                    print(f"⚠️ OCI Api Notice: {e.message}")
            time.sleep(60)

    except Exception as e:
        print(f"❌ Background Process Pipeline Error: {str(e)}")

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
        
        # Deploy thread processing task asynchronously to clear user dashboard view context
        thread = threading.Thread(
            target=run_automated_creation, 
            args=(config, data, compute_client, network_client, identity_client), 
            daemon=True
        )
        thread.start()
        
        return jsonify({"success": True, "message": "Credentials verified! Network auto-discovery and background launch loops activated successfully."})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
