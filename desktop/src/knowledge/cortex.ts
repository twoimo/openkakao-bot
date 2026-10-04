import * as THREE from 'three';

/** Static, stylized folded tissue. Anatomy is a backdrop, never a knowledge node or hit target. */
export function createCorticalBackdrop(): THREE.Group {
  const cortex=new THREE.Group();cortex.name='cortical-backdrop';
  const geometry=new THREE.SphereGeometry(1,48,32), attribute=geometry.getAttribute('position');
  for(let i=0;i<attribute.count;i++){
    const x=attribute.getX(i),y=attribute.getY(i),z=attribute.getZ(i);
    const phi=Math.atan2(z,x),theta=Math.acos(Math.max(-1,Math.min(1,y)));
    const fold=1+.055*Math.sin(phi*11+2*Math.sin(theta*4))*Math.sin(theta*8)+.025*Math.sin(phi*19-theta*7);
    attribute.setXYZ(i,x*fold,y*fold,z*fold);
  }
  geometry.computeVertexNormals();
  const material=new THREE.MeshStandardMaterial({color:'#7c8da1',roughness:.86,metalness:.3,transparent:true,opacity:.035,depthWrite:false,forceSinglePass:true});
  for(const side of [-1,1]){
    const lobe=new THREE.Mesh(geometry,material);lobe.scale.set(side*1.1,1.0,.8);lobe.position.set(side*.66,.05,-.2);lobe.renderOrder=-2;cortex.add(lobe);
  }
  const lines:number[]=[];
  for(const side of [-1,1])for(let band=0;band<10;band++){
    const y=-.88+band*.195,ring=Math.sqrt(Math.max(0,1-y*y));
    for(let i=0;i<64;i++)for(const step of [i,i+1]){
      const angle=step*Math.PI*2/64,fold=1+.045*Math.sin(angle*9+band*.7);
      lines.push(side*.66+Math.cos(angle)*ring*1.1*fold,y+.05,Math.sin(angle)*ring*.8*fold-.2);
    }
  }
  const contours=new THREE.BufferGeometry();contours.setAttribute('position',new THREE.Float32BufferAttribute(lines,3));
  cortex.add(new THREE.LineSegments(contours,new THREE.LineBasicMaterial({color:'#8096ad',transparent:true,opacity:.085,depthWrite:false})));
  return cortex;
}
